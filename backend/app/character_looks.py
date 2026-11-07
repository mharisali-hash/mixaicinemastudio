"""Character Looks: one character in several outfits, settings and poses, each with its own picture.

A look's picture is a Qwen-Edit of the character's full-body hero panel (or the portrait when there's no hero
yet): the same person, now wearing the outfit, in the setting, in the pose. Look Packs make four at once.
Performance (how the character moves on camera) is kept on the character and read by every talking video.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models_catalog
from app.models import Character, CharacterLook, Generation
from app.services import enqueue_generation, gen_out

TARGET = "character_look"
KIND = "portrait"
MAX_LOOKS = 24
FINISHED = ("ready", "approved")

# four looks each; the words go straight into the picture prompt
PACKS: dict[str, dict] = {
    "business": {"label": "Business", "looks": [
        ("Office suit", "a well-fitted navy suit with a crisp white shirt", "a bright modern office", "standing, hands loosely together"),
        ("Smart casual", "a tailored blazer over a plain knit top", "a meeting room with glass walls", "seated at a table, leaning slightly forward"),
        ("Presenter", "a charcoal suit, no tie", "in front of a large screen in a conference hall", "standing, one hand gesturing"),
        ("Desk", "a light shirt with rolled sleeves", "a tidy desk with a laptop", "sitting at the desk, looking at the camera"),
    ]},
    "casual": {"label": "Casual", "looks": [
        ("Weekend", "a soft sweater and jeans", "a cosy living room", "sitting on a sofa, relaxed"),
        ("Café", "a light jacket over a t-shirt", "a sunny café", "sitting at a small table with a cup"),
        ("Park", "a hoodie and trainers", "a green park in the afternoon", "walking towards the camera"),
        ("Kitchen", "a simple shirt and an apron", "a warm home kitchen", "standing at the counter, smiling"),
    ]},
    "traditional": {"label": "Traditional", "looks": [
        ("Shalwar kameez", "a crisp white shalwar kameez", "a courtyard of an old haveli", "standing, hands at the sides"),
        ("Sherwani", "an embroidered cream sherwani", "a festive hall with warm lights", "standing, three-quarter turn"),
        ("Dupatta", "a kurta with a softly draped dupatta", "a garden with jasmine and soft light", "standing, gentle smile"),
        ("Waistcoat", "a kurta with a dark waistcoat", "a quiet street in an old city at golden hour", "walking slowly"),
    ]},
    "presenter": {"label": "News presenter", "looks": [
        ("Anchor", "a sharp blazer, neat and formal", "a television news desk with soft blue lights", "seated behind the desk, facing the camera"),
        ("Reporter", "a smart jacket with a lapel microphone", "a city street, slightly blurred behind", "standing, holding a microphone"),
        ("Studio host", "a modern open-collar shirt and jacket", "a bright talk-show studio", "standing beside a tall table"),
        ("Explainer", "a clean polo shirt", "in front of a plain light wall", "standing, one hand open as if explaining"),
    ]},
    "outdoor": {"label": "Outdoor", "looks": [
        ("Mountains", "a warm puffer jacket and a scarf", "a mountain view with clear sky", "standing, looking out then at the camera"),
        ("Beach", "light linen clothes", "a calm beach at sunset", "walking along the shore"),
        ("City", "a trench coat", "a busy city street at dusk", "standing on the pavement, three-quarter turn"),
        ("Garden", "a light summer outfit", "a flower garden in the morning", "sitting on a bench"),
    ]},
}

EXPRESSIVENESS = {
    "calm": "a calm, composed face and very few gestures",
    "natural": "a natural expression with small, natural hand gestures",
    "lively": "a lively, expressive face and animated hand gestures",
}


def prompt_for(c: Character, look: CharacterLook) -> str:
    bits = [f"Picture 1 shows {c.name}. Make a photorealistic picture of exactly this person: keep the face, "
            "features, skin tone, hair, build and age exactly the same."]
    if look.outfit.strip():
        bits.append(f"Now wearing {look.outfit.strip()}.")
    if look.pose.strip():
        bits.append(f"Pose: {look.pose.strip()}.")
    if look.setting.strip():
        bits.append(f"Setting: {look.setting.strip()}.")
    if look.prompt.strip():
        bits.append(look.prompt.strip())
    bits.append("Three-quarter or full-body framing, natural light, sharp focus on the face.")
    return " ".join(bits)


def reference(db: Session, c: Character) -> Generation:
    """The full-body hero panel when there is one, else the portrait; approved first, then the newest ready."""
    def best(q):
        approved = db.scalars(q.where(Generation.status == "approved").order_by(Generation.created_at.desc())).first()
        return approved or db.scalars(q.where(Generation.status == "ready")
                                      .order_by(Generation.created_at.desc())).first()

    base = select(Generation).where(Generation.target_type == "character", Generation.target_id == c.id,
                                    Generation.file_path.is_not(None))
    heroes = [g for g in db.scalars(base.where(Generation.kind == "sheet_view")).all()
              if (g.params or {}).get("panel") == "hero" and g.status in FINISHED]
    if heroes:
        return max(heroes, key=lambda g: (g.status == "approved", g.created_at))
    g = best(base.where(Generation.kind == "portrait"))
    if g is None:
        raise HTTPException(409, f"Make {c.name}'s portrait first: every look is drawn from it")
    return g


def picture(db: Session, look: CharacterLook) -> Generation | None:
    rows = db.scalars(select(Generation).where(Generation.target_type == TARGET, Generation.target_id == look.id,
                                               Generation.status != "rejected")
                      .order_by(Generation.version.desc())).all()
    return next((g for g in rows if g.status == "approved"), None) or next(
        (g for g in rows if g.status == "ready"), None) or (rows[0] if rows else None)


def queue_picture(db: Session, c: Character, look: CharacterLook, note: str = "") -> Generation:
    ref = reference(db, c)
    prompt = prompt_for(c, look) + (f" {note.strip()}" if note.strip() else "")
    params = models_catalog.apply_image_choice({"reference_ids": [ref.id], "look": look.id}, None)
    return enqueue_generation(db, workspace_id=c.workspace_id, project_id=c.project_id, target_type=TARGET,
                              target_id=look.id, kind=KIND, prompt=prompt, params=params)


def ref_key(look_id: str) -> str:
    return f"look:{look_id}"


def finished_picture(db: Session, c: Character, key_or_id: str | None) -> Generation | None:
    """A look's finished picture, from "look:<id>" or a bare id, only if the look is this character's."""
    lid = (key_or_id or "").removeprefix("look:")
    look = db.get(CharacterLook, lid) if lid else None
    if look is None or look.character_id != c.id:
        return None
    g = picture(db, look)
    return g if g is not None and g.status in FINISHED and g.file_path else None


def looks_of(db: Session, c: Character) -> list[CharacterLook]:
    return list(db.scalars(select(CharacterLook).where(CharacterLook.character_id == c.id)
                           .order_by(CharacterLook.sort, CharacterLook.created_at)).all())


def add(db: Session, c: Character, *, name: str, outfit: str = "", setting: str = "", pose: str = "",
        prompt: str = "", make: bool = True) -> CharacterLook:
    looks = looks_of(db, c)
    if len(looks) >= MAX_LOOKS:
        raise HTTPException(422, f"A character can have up to {MAX_LOOKS} looks; delete one first")
    look = CharacterLook(workspace_id=c.workspace_id, character_id=c.id, name=name.strip()[:120] or "Look",
                         outfit=outfit.strip(), setting=setting.strip(), pose=pose.strip(), prompt=prompt.strip(),
                         is_default=not looks, sort=len(looks))
    db.add(look)
    db.flush()
    if make:
        queue_picture(db, c, look)
    return look


def add_pack(db: Session, c: Character, pack: str) -> list[CharacterLook]:
    p = PACKS.get(pack)
    if p is None:
        raise HTTPException(422, f"Unknown look pack '{pack}' (choose one of: {', '.join(PACKS)})")
    reference(db, c)  # before any row is made: a pack with no portrait to draw from is refused whole
    return [add(db, c, name=n, outfit=o, setting=s, pose=po) for n, o, s, po in p["looks"]]


def set_default(db: Session, c: Character, look: CharacterLook) -> None:
    for other in looks_of(db, c):
        other.is_default = other.id == look.id


def remove(db: Session, c: Character, look: CharacterLook) -> None:
    was_default = look.is_default
    for g in db.scalars(select(Generation).where(Generation.target_type == TARGET,
                                                 Generation.target_id == look.id)).all():
        g.status = "rejected"
    db.delete(look)
    db.flush()
    if was_default:
        rest = looks_of(db, c)
        if rest:
            rest[0].is_default = True


def look_out(db: Session, look: CharacterLook) -> dict:
    g = picture(db, look)
    return {"id": look.id, "character_id": look.character_id, "name": look.name, "outfit": look.outfit,
            "setting": look.setting, "pose": look.pose, "prompt": look.prompt, "is_default": look.is_default,
            "sort": look.sort, "picture": gen_out(g).model_dump() if g is not None else None,
            "created_at": look.created_at}


def performance_text(c: Character | None) -> str:
    """How this character moves on camera, for the talking-video prompts; '' when nothing was set."""
    perf = (c.performance or {}) if c is not None else {}
    bits = []
    if perf.get("expressiveness") in EXPRESSIVENESS:
        bits.append(EXPRESSIVENESS[perf["expressiveness"]])
    if (perf.get("gestures") or "").strip():
        bits.append(perf["gestures"].strip().rstrip("."))
    return ("; ".join(bits) + ".") if bits else ""
