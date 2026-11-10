"""Brand kits (contract v7): assets, prompt context, brand references, the logo check, and the
post-processing jobs (brand_apply, brand_reveal).

PLAN-m8 B2: the brand belongs *inside* the advert. Logo and product images are reference images for
the edit models (brand_reference_set) and the vision model checks the result (check_logo). The exact
extras (watermark, cards, lower third, grade) are optional and all off by default; they run as a
brand_apply job that adds a new version, so the clean one stays available.
"""
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageFont
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import uploads
from app.brand_render import BrandRenderError, KitAssets, apply_to_image, apply_to_video, logo_reveal_clip
from app.brand_schemas import BrandSettings, legacy_social, merged_settings
from app.config import get_settings
from app.llm import LLMError, chat_sync
from app.models import BrandKit, Generation, Job, MediaItem, Project, new_id
from app.services import generation_file, next_version
from app.vision import VisionScore
from app.workdirs import job_workdir

log = logging.getLogger("mixai.brand")

BRAND_JOB = "brand_apply"
REVEAL_JOB = "brand_reveal"
MB = 1024 * 1024
FONT_MAX = 10 * MB
LOGO_MAX = 5 * MB
SVG_MAX_SIDE = 2048
REF_MAX = 6
EDIT_MAX_REFS = 3  # Qwen-Image-Edit 2511; the M7 catalog's max_refs wins when it's passed in
FINISHED = ("ready", "approved")
VIDEO_KINDS = ("render", "take")
IMAGE_KINDS = ("portrait", "sheet_view", "establishing", "keyframe_start", "keyframe_end", "keyframe_mid", "image")
# params describing how the *source* was made; the branded version shouldn't claim them
DROP_KEYS = ("comfy", "upscale_stats", "segments", "created_by", "original_name", "bytes", "autopilot",
             "auto_check", "auto_check_reason", "auto_issues", "approved_by", "compile_first", "brand",
             "mezzanines", "loudness", "join", "fallbacks", "input_hash")
REVEAL_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}


class BrandError(RuntimeError):
    pass


# ---------------------------------------------------------------- uploads

def sniff_font(head: bytes) -> str | None:
    if head[:4] in (b"\x00\x01\x00\x00", b"true"):
        return "font/ttf"
    if head[:4] == b"OTTO":
        return "font/otf"
    return None


def sniff_svg(head: bytes) -> str | None:
    h = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return "image/svg+xml" if h.startswith((b"<?xml", b"<svg", b"<!--")) else None


def sniff_logo(head: bytes) -> str | None:
    t = uploads.sniff(head)
    if t in ("image/png", "image/webp"):
        return t
    return sniff_svg(head)


def sniff_photo(head: bytes) -> str | None:
    t = uploads.sniff(head)
    # brand products go straight into compositing; RAW/HEIC/TIFF only belong in the photo catalogue
    return t if t and t.startswith("image/") and t not in uploads.CONVERTED else None


def _limit_err(text: str):
    return lambda _t: HTTPException(413, text)


PROFILES = {
    "font": uploads.Profile(sniff_font, lambda _t: FONT_MAX, _limit_err("Font files can be at most 10 MB"),
                            "a TrueType (.ttf) or OpenType (.otf) font", FONT_MAX),
    "logo": uploads.Profile(sniff_logo, lambda _t: LOGO_MAX, _limit_err("Logos can be at most 5 MB"),
                            "a PNG or WebP logo with transparency, or an SVG, up to 5 MB", LOGO_MAX),
    "product": uploads.Profile(sniff_photo, lambda _t: uploads.IMAGE_MAX, _limit_err("Images can be at most 40 MB"),
                               "a PNG, JPEG or WebP image up to 40 MB", uploads.IMAGE_MAX),
}
PROFILES["reference"] = PROFILES["product"]


def validate_font(path: Path) -> str:
    """Load it the way the card renderer will. Returns 'Family Style'."""
    try:
        font = ImageFont.truetype(str(path), 32)
        font.getbbox("Ab")
        family, style = font.getname()
    except (OSError, ValueError):
        raise HTTPException(415, "This font file is damaged or not really a TTF/OTF font") from None
    return " ".join(x for x in (family, style) if x) or "Font"


# resvg never runs scripts, but it would follow file references and entity tricks; refuse those outright
_SVG_BAD = re.compile(r"<!DOCTYPE|<!ENTITY|<script|<foreignObject|\bon[a-z]+\s*=", re.I)
_SVG_HREF = re.compile(r"""(?:xlink:)?href\s*=\s*["']\s*([^"']*)["']""", re.I)


def check_svg(text: str) -> None:
    if "<svg" not in text[:4096].lower() and "<svg" not in text.lower():
        raise HTTPException(415, "This file isn't an SVG image")
    if _SVG_BAD.search(text):
        raise HTTPException(415, "This SVG has scripts, entities or embedded HTML; export a plain SVG or a PNG")
    for href in _SVG_HREF.findall(text):
        h = href.strip()
        if h and not h.startswith("#") and not h.startswith("data:image/"):
            raise HTTPException(415, "This SVG links to outside files; embed them or export a PNG")


def rasterise_svg(src: Path, dest: Path) -> tuple[int, int]:
    """SVG to a transparent PNG with resvg (in-process Rust, no system libraries), long side 2048."""
    import resvg_py

    text = src.read_text(encoding="utf-8", errors="replace")
    check_svg(text)
    empty = dest.parent / f".svgres-{new_id()}"
    empty.mkdir(parents=True, exist_ok=True)
    try:
        # first pass at natural size to learn the aspect, then the real one
        png = bytes(resvg_py.svg_to_bytes(svg_string=text, resources_dir=str(empty), skip_system_fonts=False))
        dest.write_bytes(png)
        with Image.open(dest) as im:
            w, h = im.size
        s = SVG_MAX_SIDE / max(w, h)
        if abs(s - 1) > 0.01:
            png = bytes(resvg_py.svg_to_bytes(svg_string=text, resources_dir=str(empty), width=round(w * s),
                                              height=round(h * s)))
            dest.write_bytes(png)
        with Image.open(dest) as im:
            return im.size
    except ValueError as e:
        raise HTTPException(415, f"Couldn't draw this SVG: {e}") from None
    finally:
        shutil.rmtree(empty, ignore_errors=True)


def has_alpha(path: Path) -> bool:
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
            lo, _hi = im.convert("RGBA").getchannel("A").getextrema()
            return lo < 255
    return False


def store_brand_upload(db: Session, workspace_id: str, user_id: str, got: uploads.Received, folder: Path,
                       rel_folder: str, purpose: str) -> tuple[MediaItem, list[str]]:
    warnings: list[str] = []
    name = uuid_hex()
    params = {"original_name": got.filename[:300], "bytes": got.size, "brand_asset": purpose,
              "created_by": {"user_id": user_id, "flow": f"brand_upload:{purpose}"}}
    if purpose == "font":
        family = validate_font(got.path)
        final = folder / f"{name}{uploads.EXT[got.media_type]}"
        got.path.replace(final)
        kind, media_type, w, h = "font", got.media_type, None, None
        params["family"] = family
        title = got.title or family
    else:
        if got.media_type == "image/svg+xml":
            final = folder / f"{name}.png"
            try:
                w, h = rasterise_svg(got.path, final)
            except BaseException:
                final.unlink(missing_ok=True)
                raise
            got.path.unlink(missing_ok=True)
            media_type = "image/png"
            params["original_format"] = "svg"
        else:
            info = uploads.probe_image(got.path, got.media_type)
            w, h = info.width, info.height
            final = folder / f"{name}{uploads.EXT[got.media_type]}"
            got.path.replace(final)
            media_type = got.media_type
        kind = "image"
        params["size"] = [w, h]
        if purpose == "logo":
            params["has_alpha"] = has_alpha(final)
            if not params["has_alpha"]:
                warnings.append("This logo has no transparent background, so it will show as a box when placed "
                                "over a picture. A PNG with transparency looks better.")
        title = got.title or Path(got.filename).stem or purpose.title()
    item = MediaItem(id=new_id(), workspace_id=workspace_id, kind=kind, origin="upload", title=title[:300],
                     tags=["brand", purpose], width=w, height=h, home="brand")
    g = Generation(workspace_id=workspace_id, project_id=None, target_type="media", target_id=item.id, kind="upload",
                   version=1, status="ready", prompt="", params=params, seed=0,
                   file_path=f"{rel_folder}/{final.name}", media_type=media_type)
    db.add(item)
    db.add(g)
    db.flush()
    item.generation_id = g.id
    db.commit()
    return item, warnings


def uuid_hex() -> str:
    return new_id().replace("-", "")


# ---------------------------------------------------------------- lookups

def media_generation(db: Session, workspace_id: str, media_id: str | None) -> Generation | None:
    """A MediaItem's current version, or a generation id used directly (project stills)."""
    if not media_id:
        return None
    item = db.get(MediaItem, media_id)
    if item is not None and item.workspace_id == workspace_id:
        g = db.get(Generation, item.generation_id) if item.generation_id else None
    else:
        g = db.get(Generation, media_id)
        if g is not None and g.workspace_id != workspace_id:
            g = None
    if g is None or g.status not in FINISHED or not g.file_path:
        return None
    return g


def media_file(db: Session, workspace_id: str, media_id: str | None) -> Path | None:
    g = media_generation(db, workspace_id, media_id)
    path = generation_file(g) if g else None
    return path if path and path.is_file() else None


def get_kit(db: Session, workspace_id: str, kit_id: str | None = None, project_id: str | None = None) -> BrandKit | None:
    """kit_id (or "default"), else the project's kit. Never someone else's kit."""
    if not kit_id and project_id:
        p = db.get(Project, project_id)
        kit_id = ((p.settings or {}).get("brand_kit_id") if p is not None else None) or None
    if not kit_id:
        return None
    if kit_id == "default":
        return db.scalars(select(BrandKit).where(BrandKit.workspace_id == workspace_id,
                                                 BrandKit.is_default.is_(True))).first()
    kit = db.get(BrandKit, kit_id)
    return kit if kit is not None and kit.workspace_id == workspace_id else None


def contact_of(kit: BrandKit | None) -> dict[str, str]:
    """The kit's filled contact fields; the pre-0024 `social` line lives in socials now."""
    if kit is None:
        return {}
    return {k: v for k, v in (kit.contact or {}).items() if k != "social" and isinstance(v, str) and v.strip()}


def socials_of(kit: BrandKit | None) -> list[dict]:
    if kit is None:
        return []
    rows = [dict(s) for s in kit.socials or [] if isinstance(s, dict) and s.get("handle")]
    if not rows:
        # a kit row the 0024 migration hasn't seen (or a test database made from the models)
        old = legacy_social((kit.contact or {}).get("social") or "")
        rows = [old] if old else []
    return rows


def logo_ref(kit: BrandKit, variant: str = "primary") -> dict:
    return (kit.logos or {}).get(variant) or {}


def kit_assets(db: Session, kit: BrandKit, settings: BrandSettings | None = None) -> KitAssets:
    ws = kit.workspace_id
    fonts = [p for p in (media_file(db, ws, m) for m in kit.font_files or []) if p]
    return KitAssets(
        name=kit.name, tagline=kit.tagline or "", palette=list(kit.palette or []),
        logo=media_file(db, ws, logo_ref(kit).get("media_id")),
        logo_light=media_file(db, ws, logo_ref(kit, "light").get("media_id")),
        logo_dark=media_file(db, ws, logo_ref(kit, "dark").get("media_id")),
        font=fonts[0] if fonts else None,
        settings=settings or merged_settings(kit.settings),
    )


# ---------------------------------------------------------------- prompt side

def _palette_phrase(kit: BrandKit) -> str:
    cols = []
    for p in kit.palette or []:
        name = (p.get("name") or "").strip()
        cols.append(f"{name} {p['hex']}" if name else p["hex"])
    return ", ".join(cols)


def prompt_context(kit: BrandKit) -> str:
    """The kit as a text block for the LLM (scripts, prompts, captions): name, palette, look, voice."""
    lines = [f"Brand: {kit.name}"]
    if kit.tagline:
        lines.append(f"Tagline: {kit.tagline}")
    if kit.palette:
        lines.append(f"Colour palette: {_palette_phrase(kit)}")
    if kit.style_text:
        lines.append(f"Look and feel: {kit.style_text.strip()}")
    if kit.voice_text:
        lines.append(f"Tone of voice: {kit.voice_text.strip()}")
    prods = [p for p in kit.products or [] if p.get("name")]
    if prods:
        lines.append("Products: " + "; ".join(
            f"{p['name']}" + (f" ({p['description']})" if p.get("description") else "") for p in prods))
    if logo_ref(kit).get("description"):
        lines.append(f"Logo: {logo_ref(kit)['description']}")
    return "\n".join(lines)


def image_prompt_suffix(kit: BrandKit) -> str:
    """What an image prompt carries: palette and look only (tone of voice means nothing to a diffusion model)."""
    parts = []
    if kit.palette:
        parts.append(f"colour palette: {_palette_phrase(kit)}")
    if kit.style_text:
        parts.append(kit.style_text.strip().rstrip("."))
    return ", ".join(parts)


def brand_refs(kit: BrandKit) -> list[str]:
    """Mood/product references for edit models: products first, then the kit's references. Media ids."""
    out = [p["media_id"] for p in kit.products or [] if p.get("media_id")]
    out += list(kit.reference_media_ids or [])
    return list(dict.fromkeys(out))[:REF_MAX]


@dataclass
class BrandRefSet:
    media_ids: list[str] = field(default_factory=list)  # in priority order, at most max_refs
    labels: list[str] = field(default_factory=list)  # one per media id, for the "Picture N shows ..." preamble
    prompt: str = ""  # names the surfaces, appended to the frame prompt
    dropped: list[str] = field(default_factory=list)  # placements that didn't fit under max_refs


def _asset(kit: BrandKit, pl: dict) -> tuple[str | None, str, str]:
    """(media id, label, phrase subject) for one placement."""
    aid = pl.get("asset_id")
    if pl.get("asset_type") == "logo":
        logos = kit.logos or {}
        ref = next((v for v in logos.values() if isinstance(v, dict) and v.get("media_id") == aid), None) \
            or logo_ref(kit)
        desc = (ref.get("description") or "").strip()
        subject = f"the {kit.name} logo" + (f" ({desc})" if desc else "")
        return ref.get("media_id"), f"the {kit.name} logo", subject
    prod = next((p for p in kit.products or [] if p.get("media_id") == aid), None)
    if prod is None:
        return None, "", ""
    desc = (prod.get("description") or "").strip()
    return prod["media_id"], f"the {prod['name']}", f"the {prod['name']}" + (f" ({desc})" if desc else "")


def placement_phrase(subject: str, pl: dict, asset_type: str) -> str:
    surface = (pl.get("surface") or "").strip()
    hero = pl.get("prominence") == "hero"
    if asset_type == "logo":
        where = f" printed on the {surface}" if surface else " clearly visible"
        tail = ", front-facing, sharp, legible" if hero else ", visible in the background, undistorted"
    else:
        where = f" on the {surface}" if surface else ""
        tail = ", in the foreground, sharp, true to the reference" if hero else ", visible in the background"
    return f"{subject}{where}{tail}"


def brand_reference_set(kit: BrandKit, placements: list[dict], max_refs: int = EDIT_MAX_REFS,
                        character_ref_ids: list[str] | None = None) -> BrandRefSet:
    """Reference images for one frame: hero placements first, then characters (faces must hold), then
    background placements, capped at max_refs. character_ref_ids pass through untouched (they are
    whatever ids the caller already uses), so the result can go straight into params.reference_ids
    after resolve_reference_ids() turns the brand media ids into generation ids."""
    max_refs = max(0, int(max_refs))
    hero = [p for p in placements or [] if p.get("prominence") == "hero"]
    rest = [p for p in placements or [] if p.get("prominence") != "hero"]
    out = BrandRefSet()
    phrases: list[str] = []

    def add(mid: str, label: str) -> bool:
        if mid in out.media_ids:
            return True
        if len(out.media_ids) >= max_refs:
            return False
        out.media_ids.append(mid)
        out.labels.append(label)
        return True

    def place(pl: dict) -> None:
        mid, label, subject = _asset(kit, pl)
        if not mid:
            out.dropped.append(pl.get("asset_id") or "?")
            return
        if add(mid, label):
            phrases.append(placement_phrase(subject, pl, pl.get("asset_type", "product")))
        else:
            out.dropped.append(mid)
            # no reference slot left: the prompt still asks for it, the model draws it from words alone
            phrases.append(placement_phrase(subject, pl, pl.get("asset_type", "product")))

    for pl in hero:
        place(pl)
    for cid in character_ref_ids or []:
        add(cid, "a character")
    for pl in rest:
        place(pl)
    out.prompt = "; ".join(phrases)
    return out


def resolve_reference_ids(db: Session, workspace_id: str, media_ids: list[str]) -> list[str]:
    """Brand media ids -> generation ids (what the comfy driver's reference_ids expects). Ids that are
    already generation ids, or missing, pass through / drop out."""
    out = []
    for mid in media_ids:
        g = media_generation(db, workspace_id, mid)
        if g is not None:
            out.append(g.id)
        elif db.get(Generation, mid) is not None:
            out.append(mid)
    return out


def apply_to_request(db: Session, workspace_id: str, kit_id: str | None, prompt: str, params: dict, *,
                     edit: bool = False, max_refs: int = EDIT_MAX_REFS) -> tuple[str, dict]:
    """Generation-time hook (image generate/edit, quick): adds the palette/look to the prompt, product refs
    to an edit when there's room, and marks the output for the automatic brand pass. A no-op without a kit."""
    if not kit_id:
        return prompt, params
    kit = get_kit(db, workspace_id, kit_id)
    if kit is None:
        raise HTTPException(404, "Brand kit not found")
    suffix = image_prompt_suffix(kit)
    if suffix:
        prompt = f"{prompt.strip().rstrip('.')}. {suffix}"
    params = dict(params)
    if edit:
        have = list(params.get("reference_ids") or [])
        room = max(0, max_refs - len(have))
        extra = [r for r in resolve_reference_ids(db, workspace_id, brand_refs(kit)) if r not in have][:room]
        if extra:
            params["reference_ids"] = have + extra
    params["brand"] = {"kit_id": kit.id, "auto": True}
    return prompt, params


# ---------------------------------------------------------------- logo check (vision)

class LogoCheck(VisionScore):
    """score/issues (and the "7/10" clamping) come from the frame scorer."""
    present: bool = False
    legible: bool = False
    distorted: bool = False
    score: float = Field(0, ge=0, le=10)


LOGO_SYSTEM = "You check brand logos in advertising frames. Answer only with JSON."


def logo_check_messages(image: Path, logo: Path, surface: str | None = None) -> list[dict]:
    from app.vision import image_b64

    where = f" It should appear on: {surface.strip()[:200]}." if surface else ""
    text = ("The FIRST image is an advertising frame. The SECOND image is the brand's real logo." + where +
            " Is the logo present in the frame? Is it legible (shapes and any lettering readable)? Is it distorted "
            "(warped letters, wrong colours, extra or missing parts)? Score 0-10 how faithfully the frame shows "
            'the logo. Reply as JSON {"present": bool, "legible": bool, "distorted": bool, "score": 0-10, '
            '"issues": ["short phrase", ...]}.')
    return [{"role": "system", "content": LOGO_SYSTEM},
            {"role": "user", "content": text, "images": [image_b64(image), image_b64(logo)]}]


def check_logo(image_path: Path, logo_path: Path, surface: str | None = None, *, tick=None) -> dict:
    """Vision verdict on a frame. Never raises for model trouble: {"checked": False, "reason": ...} instead,
    so callers (regenerate loops, review flags) can treat "couldn't check" differently from "failed"."""
    try:
        msgs = logo_check_messages(image_path, logo_path, surface)
    except OSError as e:
        return {"checked": False, "reason": f"couldn't read the images: {e}"}
    try:
        res = chat_sync("vision", msgs, schema=LogoCheck, temperature=0.1, max_tokens=400,
                        timeout=get_settings().vision_timeout_s, tick=tick)
    except LLMError as e:
        return {"checked": False, "reason": str(e) or "vision model unavailable"}
    d: LogoCheck = res.data
    passed = d.present and d.legible and not d.distorted and d.score >= 6
    return {"checked": True, "passed": passed, **d.model_dump(), "call": res.call_info("vision")}


# ---------------------------------------------------------------- brand_apply

def is_video(g: Generation) -> bool:
    return (g.media_type or "").startswith("video/") and (g.kind in VIDEO_KINDS or g.target_type == "media")


def is_image(g: Generation) -> bool:
    return (g.media_type or "").startswith("image/") and (g.kind in IMAGE_KINDS or g.kind == "upload")


IMAGE_STEPS = ("watermark", "grade")


def queue_brand_apply(db: Session, source: Generation, kit: BrandKit, options: dict | None = None, *,
                      user_id: str | None = None, flow: str = "brand", priority: int = 0) -> Job:
    if source.status not in FINISHED or not source.file_path:
        raise BrandError("This item isn't finished yet")
    path = generation_file(source)
    if not path or not path.is_file():
        raise BrandError("This item's file is missing")
    video = is_video(source)
    if not video and not is_image(source):
        raise BrandError("Only images and videos can be branded")
    st = merged_settings(kit.settings, options)
    steps = st.enabled() if video else [s for s in st.enabled() if s in IMAGE_STEPS]
    if not steps:
        raise BrandError("Nothing to apply: turn on " + (
            "the watermark, an intro or end card, the lower third or the colour grade" if video
            else "the watermark or the colour grade") + " in the kit or the options")
    if "watermark" in steps and not any(logo_ref(kit, v).get("media_id") for v in ("primary", "light", "dark")):
        raise BrandError("This kit has no logo yet; upload one before turning the watermark on")

    snap = st.model_dump()
    for g in db.scalars(select(Generation).where(Generation.parent_id == source.id,
                                                 Generation.status.in_(("queued", "generating")))):
        b = (g.params or {}).get("brand") or {}
        if b.get("kit_id") == kit.id and b.get("settings") == snap and g.job_id:
            return db.get(Job, g.job_id)

    if source.kind == "upload":
        kind = "video" if video else "image"
    else:
        kind = source.kind
    params = {k: v for k, v in (source.params or {}).items() if k not in DROP_KEYS}
    params["brand"] = {"kit_id": kit.id, "kit_name": kit.name, "applied": steps, "settings": snap,
                       "source_id": source.id, "auto": flow == "auto"}
    params["created_by"] = {"user_id": user_id, "flow": flow}
    g = Generation(id=new_id(), workspace_id=source.workspace_id, project_id=source.project_id,
                   target_type=source.target_type, target_id=source.target_id, kind=kind,
                   version=next_version(db, source.target_type, source.target_id, kind), status="queued",
                   prompt=source.prompt, params=params, seed=source.seed, parent_id=source.id)
    db.add(g)
    db.flush()
    job = Job(workspace_id=source.workspace_id, type=BRAND_JOB, project_id=source.project_id, generation_id=g.id,
              priority=priority, message="Waiting for a worker · brand",
              payload={"generation_id": g.id, "source_id": source.id, "kit_id": kit.id})
    db.add(job)
    db.flush()
    g.job_id = job.id
    return job


def out_path(g: Generation, ext: str) -> tuple[Path, str]:
    if g.target_type == "media":
        rel = Path("workspaces") / g.workspace_id / "media" / f"{g.id}{ext}"
    else:
        rel = Path("workspaces") / g.workspace_id / "projects" / (g.project_id or "_unassigned") / "generations" / f"{g.id}{ext}"
    return get_settings().data_dir / rel, rel.as_posix()


def _workdir(job_id: str) -> Path:
    return job_workdir("brand", job_id)


def handle_brand_apply(ctx) -> dict:
    db, job = ctx.db, ctx.job
    gen = db.get(Generation, job.generation_id) if job.generation_id else None
    if gen is None:
        raise RuntimeError("The branded version's record no longer exists")
    b = (gen.params or {}).get("brand") or {}
    source = db.get(Generation, b.get("source_id") or gen.parent_id or "")
    src = generation_file(source) if source else None
    if not src or not src.is_file():
        raise BrandError("The source file is gone; nothing to brand")
    kit = db.get(BrandKit, b.get("kit_id") or "")
    if kit is None:
        raise BrandError("The brand kit was deleted")
    st = BrandSettings.model_validate(b.get("settings") or {})
    if not is_video(source):
        st = st.model_copy(update={k: getattr(st, k).model_copy(update={"enabled": False})
                                   for k in ("intro_card", "end_card", "lower_third")})
    assets = kit_assets(db, kit, st)
    gen.status = "generating"
    db.commit()
    ctx.progress(0.1, "Applying the brand")

    work = _workdir(job.id)
    try:
        if is_video(source):
            dest, rel = out_path(gen, ".mp4")
            info = apply_to_video(src, dest, assets, st, work)
            media_type = "video/mp4"
            extra = {"size": info["size"], "duration_s": info["total_s"]}
        else:
            dest, rel = out_path(gen, ".png")
            applied = apply_to_image(src, dest, assets, st)
            with Image.open(dest) as im:
                w, h = im.size
            info = {"applied": applied}
            media_type = "image/png"
            extra = {"size": [w, h]}
    except BrandRenderError as e:
        raise BrandError(str(e)) from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.refresh(gen)
    gen.params = {**(gen.params or {}), **extra, "brand": {**b, "applied": info["applied"], "stats": info}}
    gen.file_path, gen.media_type, gen.status = rel, media_type, "ready"
    return {"file_path": rel, "media_type": media_type, **info}


# ---------------------------------------------------------------- logo reveal

def queue_logo_reveal(db: Session, kit: BrandKit, *, duration_s: float, aspect: str, background: dict,
                      show_tagline: bool, title: str | None, user_id: str | None) -> tuple[MediaItem, Job]:
    if not any(logo_ref(kit, v).get("media_id") for v in ("primary", "light", "dark")):
        raise BrandError("This kit has no logo yet")
    w, h = REVEAL_SIZES[aspect]
    bg = {"kind": background.get("kind") or "color", "color": background.get("color")}
    if bg["kind"] == "image":
        g = media_generation(db, kit.workspace_id, background.get("media_id"))
        if g is None or not (g.media_type or "").startswith("image/"):
            raise BrandError("The background image wasn't found")
        bg["generation_id"] = g.id
    item = MediaItem(id=new_id(), workspace_id=kit.workspace_id, kind="video", origin="generated",
                     title=(title or f"{kit.name} · logo reveal")[:300], tags=["brand", "logo reveal"],
                     width=w, height=h, duration_s=round(duration_s, 3))
    db.add(item)
    db.flush()
    params = {"brand_reveal": {"kit_id": kit.id, "aspect": aspect, "background": bg, "show_tagline": show_tagline},
              "brand": {"kit_id": kit.id, "kit_name": kit.name, "applied": ["logo_reveal"]},
              "size": [w, h], "duration_s": round(duration_s, 3), "fps": 24,
              "created_by": {"user_id": user_id, "flow": "logo_reveal"}}
    g = Generation(id=new_id(), workspace_id=kit.workspace_id, project_id=None, target_type="media",
                   target_id=item.id, kind="video", version=1, status="queued", prompt=f"{kit.name} logo reveal",
                   params=params, seed=0)
    db.add(g)
    db.flush()
    job = Job(workspace_id=kit.workspace_id, type=REVEAL_JOB, generation_id=g.id, message="Waiting for a worker",
              payload={"generation_id": g.id, "kit_id": kit.id})
    db.add(job)
    db.flush()
    g.job_id = job.id
    item.generation_id = g.id
    return item, job


def handle_logo_reveal(ctx) -> dict:
    db, job = ctx.db, ctx.job
    gen = db.get(Generation, job.generation_id) if job.generation_id else None
    if gen is None:
        raise RuntimeError("The logo reveal's record no longer exists")
    p = gen.params or {}
    rv = p.get("brand_reveal") or {}
    kit = db.get(BrandKit, rv.get("kit_id") or "")
    if kit is None:
        raise BrandError("The brand kit was deleted")
    assets = kit_assets(db, kit)
    bg = rv.get("background") or {}
    bg_image = None
    if bg.get("kind") == "image":
        bg_image = generation_file(db.get(Generation, bg.get("generation_id") or "")) if bg.get("generation_id") else None
        if not bg_image or not bg_image.is_file():
            raise BrandError("The background image is gone")
    from app.brand_render import bg_colour

    colour = bg_colour(assets, bg.get("color")) if bg.get("kind") != "image" else None
    gen.status = "generating"
    db.commit()
    dest, rel = out_path(gen, ".mp4")
    w, h = p.get("size") or REVEAL_SIZES["16:9"]
    try:
        info = logo_reveal_clip(assets, dest, duration_s=float(p.get("duration_s") or 3.0), size=(w, h),
                                fps=int(p.get("fps") or 24), colour=colour, bg_image=bg_image,
                                show_tagline=bool(rv.get("show_tagline", True)),
                                tick=lambda f: ctx.progress(0.05 + 0.9 * f, "Rendering the logo reveal"))
    except BrandRenderError as e:
        raise BrandError(str(e)) from None
    db.refresh(gen)
    gen.params = {**(gen.params or {}), "duration_s": info["duration_s"], "size": info["size"], "has_audio": True}
    gen.file_path, gen.media_type, gen.status = rel, "video/mp4", "ready"
    return {"file_path": rel, "media_type": "video/mp4", **info}


# ---------------------------------------------------------------- automatic pass after generation

def _auto_kit(db: Session, job: Job, g: Generation) -> BrandKit | None:
    p = g.params or {}
    req = p.get("brand") or {}
    if req.get("applied"):
        return None  # already a branded version (or an upscale of one)
    if g.target_type == "media" and g.kind in ("image", "video") and job.type == "generate":
        return get_kit(db, g.workspace_id, req.get("kit_id")) if req.get("auto") else None
    if g.target_type == "project" and g.kind == "render" and job.type in ("reel_assemble", "upscale"):
        if job.type == "reel_assemble" and p.get("autopilot"):
            ap = db.get(Job, p["autopilot"])
            if ap is not None and (ap.payload or {}).get("upscale"):
                return None  # the upscaled film gets the brand pass instead
        return get_kit(db, g.workspace_id, req.get("kit_id") if req.get("auto") else None, g.project_id)
    return None


def _repoint_autopilot(db: Session, branded: Generation) -> None:
    """Quick Create shows result.final_render_id; move it to the branded film once that exists."""
    if not branded.project_id or branded.kind != "render" or branded.status not in FINISHED:
        return
    from sqlalchemy.orm.attributes import flag_modified

    for ap in db.scalars(select(Job).where(Job.project_id == branded.project_id, Job.type == "autopilot")):
        r = ap.result or {}
        if r.get("final_render_id") and r["final_render_id"] == branded.parent_id:
            ap.result = {**r, "final_render_id": branded.id, "clean_render_id": branded.parent_id}
            flag_modified(ap, "result")


def on_job_finished(db: Session, job: Job) -> None:
    """Worker hook. A final output whose request (or project) names a kit with an output step switched on
    gets a follow-up brand_apply job. Priority -1 so a woken autopilot settles its final render first."""
    if job.status != "done" or not job.generation_id:
        return
    g = db.get(Generation, job.generation_id)
    if g is None:
        return
    if job.type == BRAND_JOB:
        _repoint_autopilot(db, g)
        return
    if job.type == REVEAL_JOB or g.status not in FINISHED or not g.file_path:
        return
    kit = _auto_kit(db, job, g)
    if kit is None:
        return
    st = merged_settings(kit.settings)
    steps = st.enabled() if is_video(g) else [s for s in st.enabled() if s in IMAGE_STEPS]
    if not steps:
        return
    try:
        queue_brand_apply(db, g, kit, None, flow="auto", priority=-1)
    except BrandError as e:
        log.info("auto brand skipped for %s: %s", g.id, e)


def project_kit_id(db: Session, project: Project, kit_id: str | None) -> None:
    if kit_id is not None and get_kit(db, project.workspace_id, kit_id) is None:
        raise HTTPException(404, "Brand kit not found")
    s = dict(project.settings or {})
    if kit_id:
        s["brand_kit_id"] = kit_id
    else:
        s.pop("brand_kit_id", None)
    project.settings = s

