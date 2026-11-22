"""Patients, photos, options and the edit that renders them (phase 1: the Folietech-style literal edit)."""
import hashlib
import io
import logging
import re
import secrets
import shutil
from datetime import date, timedelta
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import library as lib
from app.config import get_settings
from app.models import Generation, ImplantPatient, ImplantSession, utcnow
from app.implant import planning
from app.services import enqueue_generation, new_seed

# "own" is her everyday photo in her own clothes, any pose: what patients in Pakistan will share (surgeon's review,
# 24 Oct). It is always clothed; the five clinical views work as before
VIEWS = {"own": "Her own photo", "front": "Front", "left_oblique": "Left 45°", "right_oblique": "Right 45°", "left_lateral": "Left 90°",
         "right_lateral": "Right 90°"}
GARMENTS = ("garment", "topless")
PROFILES = ("low", "moderate", "moderate_plus", "high", "extra_high")
SHAPES = ("round", "teardrop")
PLACEMENTS = ("subglandular", "submuscular", "dual_plane")
FILLS = ("silicone", "saline")
EDIT_MODEL = "qwen_image_edit_2511"
MAX_PHOTO_BYTES = 25 * 1024 * 1024
# a 12 MP phone photo against the edit's ~1 MP: the new chest would be stretched 3.5x and look soft next to the
# rest. 1600 px on the long side is plenty for a consultation and keeps the two close
PHOTO_LONG_SIDE = 1600
DISCLAIMER = "Computer simulation for discussion only. Actual surgical results vary."
FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
ADULT_AGE = 18
MINOR_PHOTOS = ("This patient is under 18, so photos and photo previews aren't available. Size planning and the "
                "3D model still work.")
AGE_FLAGGED = ("A photo is waiting for the clinic to confirm the patient's date of birth again before it can be "
               "used.")

log = logging.getLogger(__name__)


def clinic_root() -> Path:
    return get_settings().data_dir / "clinic"


def patient_rel(p: ImplantPatient) -> Path:
    return Path("clinic") / p.workspace_id / "patients" / p.id


def own_patient(db: Session, user_id: str, patient_id: str) -> ImplantPatient:
    p = db.get(ImplantPatient, patient_id)
    # one surgeon's patients are nobody else's, admins included
    if p is None or p.surgeon_user_id != user_id:
        raise HTTPException(404, "Patient not found")
    return p


def touch(p: ImplantPatient) -> None:
    p.last_activity_at = utcnow()


# ---------------------------------------------------------------- age

def years(dob: date, today: date | None = None) -> int:
    today = today or utcnow().date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def age_of(p: ImplantPatient, today: date | None = None) -> int | None:
    return None if p.date_of_birth is None else years(p.date_of_birth, today)


def is_minor(p: ImplantPatient, today: date | None = None) -> bool:
    age = age_of(p, today)
    # records from before the date of birth was asked keep their 18+ tick
    return not p.adult_confirmed if age is None else age < ADULT_AGE


def dob_problem(dob: date, today: date | None = None) -> str | None:
    today = today or utcnow().date()
    if dob > today:
        return "The date of birth is in the future"
    if today.year - dob.year > 110:
        return "Check the date of birth: that's over 110 years ago"
    return None


def _age_look(path: Path) -> str | None:
    """None, "child" (clearly under 13: never kept, nothing to re-confirm) or "flag" (the surgeon looks again)."""
    from app import content_policy

    try:
        look = content_policy.minor_look(path)
    except Exception as e:
        log.warning("implant age check failed on %s: %s", path.name, e)
        return None
    if look is None:
        return None
    return "child" if content_policy.clear_child(look) else "flag"


# ---------------------------------------------------------------- photos

def save_photo(db: Session, p: ImplantPatient, view: str, garment: str, data: bytes,
               crop: tuple[float, float, float, float] | None = None) -> Generation:
    if view not in VIEWS:
        raise HTTPException(422, f"view must be one of: {', '.join(VIEWS)}")
    if garment not in GARMENTS:
        raise HTTPException(422, "garment must be garment or topless")
    if view == "own" and garment != "garment":
        raise HTTPException(422, "Her own photo is always in her clothes")
    if is_minor(p):
        raise HTTPException(409, MINOR_PHOTOS)
    if garment == "topless" and not (p.consent or {}).get("topless_ok"):
        raise HTTPException(409, "The patient hasn't consented to topless photos. Tick that consent first, or "
                                 "use a photo in a bra or top.")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "The photo is over 25 MB")
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        raise HTTPException(422, "That file isn't a photo we can read (JPEG, PNG or WebP)") from None
    if crop:
        # fractions of the photo, so the browser's crop box needs no pixel maths; mostly to cut the face off
        x, y, w, h = (max(0.0, min(1.0, v)) for v in crop)
        box = (round(x * im.width), round(y * im.height), round(min(1.0, x + w) * im.width),
               round(min(1.0, y + h) * im.height))
        if box[2] - box[0] < 64 or box[3] - box[1] < 64:
            raise HTTPException(422, "The crop is too small")
        im = im.crop(box)
    if max(im.size) > PHOTO_LONG_SIDE:
        im.thumbnail((PHOTO_LONG_SIDE, PHOTO_LONG_SIDE), Image.LANCZOS)
    g = Generation(workspace_id=p.workspace_id, project_id=None, target_type="implant_photo", target_id=p.id,
                   kind="upload", status="ready", prompt="", media_type="image/png",
                   params={"view": view, "garment": garment, "width": im.width, "height": im.height})
    db.add(g)
    db.flush()
    rel = patient_rel(p) / "photos" / f"{g.id}.png"
    dest = get_settings().data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "PNG")
    g.file_path = rel.as_posix()
    seen = _age_look(dest) if garment == "topless" else None
    if seen == "child":
        dest.unlink(missing_ok=True)
        db.delete(g)
        log.warning("implant photo refused for patient %s: looks like a child", p.id)
        raise HTTPException(422, "This photo can't be used: the person looks like a child.")
    if seen == "flag":
        # kept, but unused until the surgeon types the date of birth again (api.implant.confirm_photo_age)
        g.params = {**g.params, "age_flag": True, "age_flag_at": utcnow().isoformat()}
    touch(p)
    return g


def photos(db: Session, p: ImplantPatient) -> dict[str, Generation]:
    """The newest photo of each view."""
    out: dict[str, Generation] = {}
    for g in db.scalars(select(Generation).where(Generation.target_type == "implant_photo",
                                                 Generation.target_id == p.id)
                        .order_by(Generation.created_at.desc())):
        out.setdefault((g.params or {}).get("view"), g)
    return {v: out[v] for v in VIEWS if v in out}


# ---------------------------------------------------------------- the edit

def _volume_words(cc: int, base_width: float | None = None, bra_size: str | None = None) -> str:
    # how many cups a volume adds depends on her frame (planning.cc_per_cup), so the words follow it; with her
    # bra size known, the cup she'll wear names the step exactly (unrounded, so 350 and 425 cc differ)
    cups, exact = planning.cup_change(cc, base_width), cc / planning.cc_per_cup(base_width)
    size = "a subtle" if exact < 0.75 else "a moderate" if exact < 1.5 else "a clear" if exact < 2 else "a large"
    target = planning.target_cup(bra_size, exact)
    if target:
        return f"{size} increase in breast volume, so that she now fills {target}"
    return f"{size} increase in breast volume, {planning.cup_words(cups)}"


PROFILE_WORDS = {
    "low": "a low profile: wide and gently rounded, little forward projection",
    "moderate": "a moderate profile: natural forward projection",
    "moderate_plus": "a moderate-plus profile: a little more forward projection than natural",
    "high": "a high profile: noticeable forward projection and upper fullness",
    "extra_high": "an extra-high profile: strong forward projection and a full upper pole",
}
SHAPE_WORDS = {
    "round": "round implants, so the upper part of the breast is fuller",
    "teardrop": "anatomical teardrop implants, so the upper slope stays natural and the lower part is fuller",
}
PLACEMENT_WORDS = {
    "subglandular": "placed over the muscle, giving a rounder, more defined upper edge",
    "submuscular": "placed under the muscle, giving a soft, gradual upper slope",
    "dual_plane": "placed in a dual plane (partly under the muscle), giving a soft, natural upper slope",
}


OWN_CLOTHES = ("She wears the same clothes as in the photo (for example a kameez, kurta, dress or blouse), with any "
               "dupatta, scarf or shawl exactly where it is. Keep their colour, print, embroidery, fabric and the folds "
               "away from the bust exactly; the fabric fits and drapes naturally over the fuller bust. Do not remove, "
               "open, lift, shorten or change any clothing, and do not make it tighter or more revealing.")


def instruction(opt: dict, garment: str, measurements: dict | None = None, view: str | None = None) -> str:
    """Literal wording, as Folietech's wrap renders: the edit models follow plain words far better than
    numbers or flourishes. cc only steers the words; phase 2's warp makes the size exact."""
    left, right = int(opt["left_cc"]), int(opt["right_cc"])
    m = measurements or {}
    bw = planning._num(m, "bw_left_cm" if left >= right else "bw_right_cm")
    size = (f"Give this woman {_volume_words(max(left, right), bw, m.get('bra_size'))}"
            + (f", with the left breast ({left} cc) {'slightly larger' if left > right else 'slightly smaller'} "
               f"than the right ({right} cc)" if abs(left - right) >= 25 else ", the same on both sides")
            + f", as after breast augmentation with {SHAPE_WORDS[opt['shape']]}, "
            f"{PROFILE_WORDS[opt['profile']]}, {PLACEMENT_WORDS[opt['placement']]}"
            + (", saline-filled so slightly firmer and rounder." if opt.get("fill") == "saline"
               else ", silicone gel so soft and natural."))
    lift = ""
    if opt.get("procedure") == "augmentation_lift":
        lift = ("Also as after a breast lift (mastopexy): the breasts sit higher on the chest and look firmer and "
                "rounder, the lowest part of each breast rises to the breast crease, and "
                + ("the nipples sit higher, centred on the front of each breast. " if garment == "topless" else
                   "the bust sits higher and fuller under the clothes. " if view == "own" else
                   "the bra cups sit higher and fuller. "))
    body = OWN_CLOTHES if view == "own" else ("The nipples and areolas keep their size, colour and shape; they sit slightly higher and point "
            "slightly outward. The breast crease may sit slightly lower. Keep the natural gap between the breasts."
            if garment == "topless" else
            "She wears the same bra or top as in the photo: keep its style, colour and fabric exactly, fitted "
            "naturally over the new shape. Do not remove, open or change any clothing.")
    return " ".join([
        size, lift + body,
        "Keep everything else identical: same person, " + ("same face, " if view == "own" else "") + "same body shape outside the chest, skin tone and texture, "
        "moles, tattoos, arms, hands, pose, camera angle, crop, background and lighting.",
        "Never show more skin than the original photo. Clinical medical photograph, photorealistic and natural, "
        "no glamour, no scars. Do not add text or logos.",
    ])


ENGINES = ("ai", "warp")


def generate(db: Session, p: ImplantPatient, options: list[dict], engines: tuple[str, ...] = ("ai",)) -> ImplantSession:
    if not (p.consent or {}).get("at"):
        raise HTTPException(409, "Record the patient's consent first")
    if is_minor(p):
        raise HTTPException(409, MINOR_PHOTOS)
    pics = photos(db, p)
    if not pics:
        raise HTTPException(409, "Add at least one photo first")
    if any((g.params or {}).get("age_flag") for g in pics.values()):
        raise HTTPException(409, AGE_FLAGGED)
    s = ImplantSession(patient_id=p.id, options=options)
    db.add(s)
    db.flush()
    # one seed per view for every option: the options then differ by their spec, not by the noise
    seeds = {view: new_seed() for view in pics}
    for engine in [e for e in ENGINES if e in engines]:
        for i, opt in enumerate(options):
            for view, photo in pics.items():
                pp = photo.params or {}
                garment = pp.get("garment", "garment")
                imp = {"option": i, "view": view, "photo_id": photo.id, "garment": garment, "engine": engine}
                if engine == "warp":
                    # no model: the worker reshapes her photo itself (render_warp)
                    enqueue_generation(db, workspace_id=p.workspace_id, project_id=None,
                                       target_type="implant_result", target_id=s.id, kind="image",
                                       prompt="shape warp", params={"implant": imp})
                    continue
                w, h = lib.size_for_ratio(pp.get("width") or 3, pp.get("height") or 4)
                enqueue_generation(db, workspace_id=p.workspace_id, project_id=None, target_type="implant_result",
                                   target_id=s.id, kind="image", seed=seeds[view],
                                   prompt=instruction(opt, garment, p.measurements, view),
                                   params={"reference_ids": [photo.id], "model": EDIT_MODEL, "width": w, "height": h,
                                           "implant": imp})
    touch(p)
    return s


def results(db: Session, s: ImplantSession) -> list[Generation]:
    return list(db.scalars(select(Generation).where(Generation.target_type == "implant_result",
                                                    Generation.target_id == s.id)
                           .order_by(Generation.created_at)))


def engine_of(g: Generation) -> str:
    # results from before the shape warp have no engine: they are the AI edit
    return ((g.params or {}).get("implant") or {}).get("engine") or "ai"


def render_warp(db: Session, gen: Generation, path: Path, tick=None) -> None:
    """In the worker: the shape warp for one option and view, straight from her photo (app.implant.warp)."""
    from app.implant import finish, warp

    imp = (gen.params or {}).get("implant") or {}
    photo = db.get(Generation, imp.get("photo_id") or "")
    src = get_settings().data_dir / photo.file_path if photo is not None and photo.file_path else None
    if src is None or not src.is_file():
        raise RuntimeError("The patient's photo is gone; add it again")
    s = db.get(ImplantSession, gen.target_id)
    p = db.get(ImplantPatient, s.patient_id) if s else None
    if s is None or p is None:
        raise RuntimeError("This consultation no longer exists")
    view = imp.get("view", "front")
    pp = dict(photo.params or {})
    box = pp.get("chest_box")
    if not box:
        box, pp["chest_box_from"] = finish.find_chest(src, view, tick)
        pp["chest_box"] = box
    # points found for another box (the surgeon moved it since) are found again
    if not pp.get("bust_points") or pp.get("bust_points_box") != box:
        pp["bust_points"], pp["bust_points_from"] = finish.find_bust(src, view, box, tick)
        pp["bust_points_box"] = box
    photo.params = pp
    path.parent.mkdir(parents=True, exist_ok=True)
    used = warp.render(src, path, s.options[imp["option"]], p.measurements, view, box, pp["bust_points"])
    stamp(path)
    gen.params = {**(gen.params or {}), "chest_box": box, "warp": used}


def finish_result(db: Session, gen: Generation, path: Path, redraw, tick=None) -> None:
    """In the worker, once the edit is on disk: chest only (finish.composite), the clothing check with one
    redraw, then the stamp. `redraw(seed)` renders the edit again into `path`."""
    from app.implant import finish

    imp = (gen.params or {}).get("implant") or {}
    photo = db.get(Generation, imp.get("photo_id") or "")
    src = get_settings().data_dir / photo.file_path if photo is not None and photo.file_path else None
    if src is None or not src.is_file():
        raise RuntimeError("The patient's photo is gone; add it again")
    box = (photo.params or {}).get("chest_box")
    origin = "surgeon"
    if not box:
        box, origin = finish.find_chest(src, imp.get("view", "front"), tick)
        photo.params = {**(photo.params or {}), "chest_box": box, "chest_box_from": origin}
    finish.composite(src, path, box)
    verdict = None if imp.get("garment") == "topless" else finish.garment_check(src, path, tick)
    if verdict is False:
        redraw(new_seed())
        finish.composite(src, path, box)
        verdict = finish.garment_check(src, path, tick)
        if verdict is False:
            raise RuntimeError("The result changed the clothing; try again")
    gen.params = {**(gen.params or {}), "chest_box": box, "garment_check": verdict}
    stamp(path)


def stamp(path: Path) -> None:
    """The small "Simulation" mark every result carries, wherever it ends up."""
    with Image.open(path) as src:
        im = src.convert("RGB")
    mark(im, "SIMULATION").save(path)


def marked_png(path: Path, text: str = "CLINIC PREVIEW") -> bytes:
    # what a patient sees of a clinic photo: a marked copy, the file itself stays as it was
    with Image.open(path) as src:
        im = mark(src.convert("RGB"), text)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def mark(im: Image.Image, text: str) -> Image.Image:
    d = ImageDraw.Draw(im, "RGBA")
    size = max(14, im.width // 40)
    font = _font("inter", size)
    tw = d.textlength(text, font=font)
    pad = size // 2
    x, y = im.width - tw - pad * 3, im.height - size - pad * 3
    d.rounded_rectangle((x - pad, y - pad, x + tw + pad, y + size + pad), radius=pad, fill=(20, 18, 16, 150))
    d.text((x, y), text, font=font, fill=(250, 246, 238, 235))
    return im


# ---------------------------------------------------------------- deleting

def drop_pictures(db: Session, p: ImplantPatient) -> int:
    """Every photo, result and chart of a record, keeping the record itself (a patient found to be under 18)."""
    root = get_settings().data_dir
    sids = list(db.scalars(select(ImplantSession.id).where(ImplantSession.patient_id == p.id)))
    gens = list(db.scalars(select(Generation).where(
        ((Generation.target_type == "implant_photo") & (Generation.target_id == p.id))
        | ((Generation.target_type == "implant_result") & Generation.target_id.in_(sids)))))
    for g in gens:
        if g.file_path:
            (root / g.file_path).unlink(missing_ok=True)
        db.delete(g)
    for s in db.scalars(select(ImplantSession).where(ImplantSession.patient_id == p.id)):
        db.delete(s)
    for v in (p.chart or {}).get("versions") or []:
        if v.get("chart_path"):
            (root / v["chart_path"]).unlink(missing_ok=True)
    p.chart = {}
    return len(gens)


def purge(db: Session, p: ImplantPatient) -> None:
    """Everything the patient left behind: rows and files, nothing kept."""
    root = get_settings().data_dir
    sids = list(db.scalars(select(ImplantSession.id).where(ImplantSession.patient_id == p.id)))
    gens = list(db.scalars(select(Generation).where(
        ((Generation.target_type == "implant_photo") & (Generation.target_id == p.id))
        | ((Generation.target_type == "implant_result") & Generation.target_id.in_(sids)))))
    for g in gens:
        if g.file_path:
            (root / g.file_path).unlink(missing_ok=True)
        db.delete(g)
    for s in db.scalars(select(ImplantSession).where(ImplantSession.patient_id == p.id)):
        db.delete(s)
    # sessions before the patient, or the FK cascade gets there first
    db.flush()
    shutil.rmtree(root / patient_rel(p), ignore_errors=True)
    db.delete(p)


SESSION_ONLY_HOURS = 12
# a record the patient can open from her own account is kept for one of these, never session only
PATIENT_KEEP = (1, 3, 6)


def keep_for(p: ImplantPatient) -> timedelta:
    # retention 0 is "session only": gone when the consultation ends, or after a quiet half day at the latest
    if not p.retention_months:
        return timedelta(hours=SESSION_ONLY_HOURS)
    return timedelta(days=30 * p.retention_months)


def purge_expired(db: Session) -> int:
    now = utcnow()
    gone = [p for p in db.scalars(select(ImplantPatient)) if p.last_activity_at + keep_for(p) < now]
    for p in gone:
        purge(db, p)
    return len(gone)


# ---------------------------------------------------------------- the patient's own account

CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L: it gets read out across a desk
INVITE_DAYS = 7


def _code_hash(code: str) -> str:
    return hashlib.sha256(re.sub(r"[^A-Z0-9]", "", code.upper()).encode()).hexdigest()


def new_invite(p: ImplantPatient) -> str:
    code = "".join(secrets.choice(CODE_LETTERS) for _ in range(8))
    p.invite_code_hash = _code_hash(code)
    p.invite_expires_at = utcnow() + timedelta(days=INVITE_DAYS)
    return code


def find_invite(db: Session, code: str) -> ImplantPatient | None:
    p = db.scalar(select(ImplantPatient).where(ImplantPatient.invite_code_hash == _code_hash(code)))
    if p is None or p.invite_expires_at is None or p.invite_expires_at < utcnow():
        return None
    return p


def linked_patient(db: Session, user_id: str) -> ImplantPatient | None:
    return db.scalar(select(ImplantPatient).where(ImplantPatient.patient_user_id == user_id).limit(1))


def unlink(p: ImplantPatient) -> None:
    p.patient_user_id = None
    p.invite_code_hash = None
    p.invite_expires_at = None


def _font(family: str, size: int):
    folder = FONTS / ("playfair-display" if family == "serif" else "inter")
    f = next(folder.glob("*.ttf"), None)
    return ImageFont.truetype(str(f), size) if f else ImageFont.load_default()
