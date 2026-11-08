"""Presenter Video: a character, in one of its looks, presenting a script to camera.

The script is cut into scenes of a sentence or two. Every scene has its own voice take (the character's voice),
its own frame (the look placed in the scene's background) and its own lip-synced take (Wan S2V), each kept with
a fingerprint of what made it. Editing a scene changes its fingerprints, so only that scene is redone; the cut
(ffmpeg, captions, a name strip and a title card) always runs again and is cheap.
"""
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import character_looks as cl
from app import models_catalog as mc
from app import speech_language
from app import voices
from app.models import Character, Generation, Job, PresenterVideo, new_id

JOB = "presenter_video"
ASSEMBLE_JOB = "presenter_assemble"
TARGET = "presenter_scene"
STAGES = (("speech", "Voice"), ("frames", "Frames"), ("render", "Video"), ("assemble", "Cut"))
STAGE_KEYS = tuple(k for k, _ in STAGES)
ASPECTS = ("16:9", "9:16", "1:1")
QUALITIES = ("draft", "final")
LIVE = ("queued", "generating")
FINISHED = ("ready", "approved")
ATTEMPTS = 2
# Wan S2V lip-syncs up to three 77-frame passes at 16 fps; a scene has to fit with a little room
MAX_SCENE_S = 14.0
# about how much one scene holds: a sentence or two, roughly 10 s of speech
SCENE_WORDS = 28
MAX_SCENES = 40

BACKGROUNDS = {
    "studio": "a clean modern studio with soft grey backdrop and gentle rim light",
    "office": "a bright modern office with plants and a blurred desk behind",
    "classroom": "a friendly classroom with a whiteboard softly out of focus",
    "news_desk": "a television news desk with soft blue lights behind",
    "baithak": "a warm traditional baithak with carpets, cushions and brass lamps",
    "outdoor": "a sunny park with trees softly out of focus",
    "plain": "a plain softly lit light-grey wall",
}
DEFAULT_SETTINGS = {"aspect": "16:9", "caption_style": "pill", "background": "studio", "lower_name": "",
                    "lower_role": "", "title_card": True}


class PresenterError(RuntimeError):
    pass


def settings_of(pv: PresenterVideo) -> dict:
    return {**DEFAULT_SETTINGS, **(pv.settings or {})}


def _fp(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- scenes

_SENTENCE = re.compile(r"(?<=[.!?؟۔])\s+")


def split_script(script: str) -> list[str]:
    """Paragraphs first; a paragraph too long for one scene is cut at sentence ends, keeping a sentence or two
    together; a single over-long sentence stays whole (the voice step says if it's too long to film)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", (script or "").strip()):
        para = " ".join(para.split())
        if not para:
            continue
        cur: list[str] = []
        for sent in _SENTENCE.split(para):
            if cur and len(" ".join(cur + [sent]).split()) > SCENE_WORDS:
                out.append(" ".join(cur))
                cur = []
            cur.append(sent)
        if cur:
            out.append(" ".join(cur))
    return out[:MAX_SCENES]


def new_scene(text: str, background: str | None = None) -> dict:
    return {"id": new_id(), "text": text.strip(), "background": background or "", "speech": {}, "frame": {},
            "take": {}, "duration_s": None}


def background_of(pv: PresenterVideo, sc: dict) -> str:
    key = (sc.get("background") or "").strip() or settings_of(pv)["background"]
    return BACKGROUNDS.get(key, key)


# ---------------------------------------------------------------- what makes each part

def character_of(db: Session, pv: PresenterVideo) -> Character:
    c = db.get(Character, pv.character_id or "")
    if c is None or c.workspace_id != pv.workspace_id:
        raise PresenterError("The presenter's character is gone; pick another")
    return c


def voice_of(db: Session, pv: PresenterVideo, c: Character):
    v = voices.find_voice(db, pv.workspace_id, c.voice_id)
    if v is None:
        raise PresenterError(f"{c.name} has no voice yet: give them one on their Voice tab")
    return v


def picture_of(db: Session, pv: PresenterVideo, c: Character) -> Generation:
    """The look's picture when a look is chosen, else what looks are drawn from (hero panel or portrait)."""
    if pv.look_id:
        g = cl.finished_picture(db, c, pv.look_id)
        if g is not None:
            return g
    return cl.reference(db, c)


def speech_fp(pv: PresenterVideo, sc: dict, c: Character) -> str:
    return _fp(sc["text"], c.voice_id, pv.language)


def frame_fp(db: Session, pv: PresenterVideo, sc: dict, c: Character) -> str:
    return _fp(picture_of(db, pv, c).id, background_of(pv, sc), settings_of(pv)["aspect"])


def take_fp(sc: dict, quality: str, c: Character) -> str:
    return _fp((sc.get("speech") or {}).get("gen"), (sc.get("frame") or {}).get("gen"), quality,
               cl.performance_text(c))


def speech_request(db: Session, pv: PresenterVideo, sc: dict, c: Character) -> tuple[str, dict]:
    """The same params the Voice studio sends for one take, in the character's voice."""
    v = voice_of(db, pv, c)
    e = voices.resolve_engine(None, v, None)
    text = sc["text"]
    language = speech_language.dominant(text, pv.language, v.language)
    params = {"kind": "speech", "model": e.id, "engine": e.id, "voice_id": v.id, "language": language, "text": text,
              "text_spoken": voices.spoken_text(text, e), "aerab": True, "speed": 1.0, "duration_s": None,
              "voice": voices.voice_spec(db, v), "presenter": pv.id, "scene": sc["id"],
              "created_by": {"user_id": pv.created_by, "flow": "presenter"}}
    return text, params


def frame_request(db: Session, pv: PresenterVideo, sc: dict, c: Character) -> tuple[str, dict]:
    from app import library as lib

    st = settings_of(pv)
    w, h = lib.size_for_aspect(st["aspect"])
    ref = picture_of(db, pv, c)
    m = mc.default_for("edit")
    framing = "head and shoulders, centred" if st["aspect"] == "9:16" else "waist up, slightly off centre"
    prompt = (f"Picture 1 shows {c.name}. Make a photorealistic video frame of exactly this person: keep the face, "
              f"hair, skin tone, build and clothes exactly the same. {c.name} is presenting to camera, looking "
              f"straight into the lens with a friendly, open expression, mouth closed, {framing}. Background: "
              f"{background_of(pv, sc)}. Natural soft light, shallow depth of field, sharp focus on the face.")
    params = {"width": w, "height": h, "aspect": st["aspect"], "reference_ids": [ref.id], "reference_labels": [c.name],
              "model": m.id, "presenter": pv.id, "scene": sc["id"],
              "created_by": {"user_id": pv.created_by, "flow": "presenter"}}
    return prompt, params


def take_request(db: Session, pv: PresenterVideo, sc: dict, c: Character, quality: str, speech: Generation,
                 frame: Generation, audio_path: str, duration: float) -> tuple[str, dict]:
    manner = cl.performance_text(c) or "Natural expression, small head movements and light hand gestures."
    said = sc["text"].replace('"', "'")
    prompt = (f'{c.name} talks to the camera and says: "{said}" The mouth moves with every word, blinking. '
              f"{manner} Steady camera, soft light.")
    params = {"first_frame_id": frame.id, "speech_audio": audio_path, "duration_s": round(duration, 3),
              "aspect_ratio": settings_of(pv)["aspect"], "presenter": pv.id, "scene": sc["id"],
              "speech_generation_id": speech.id, "created_by": {"user_id": pv.created_by, "flow": "presenter"}}
    if quality == "final":
        params["quality"] = "hq"
    return prompt, params


# ---------------------------------------------------------------- the record

def check_script(scenes: list[dict], cur) -> None:
    """The words check at once (house rules), so a script that will be refused isn't queued at all; revered
    names are judged again in the worker, as for every prompt."""
    from app import content_policy
    from app.security import is_admin

    why = content_policy.quick_refusal("\n".join(s["text"] for s in scenes), admin=is_admin(cur))
    if why:
        raise HTTPException(422, why)


def create(db: Session, cur, *, character_id: str, look_id: str | None, title: str, language: str, script: str,
           settings: dict) -> PresenterVideo:
    c = db.get(Character, character_id or "")
    if c is None or c.workspace_id != cur.workspace_id:
        raise HTTPException(422, "That character isn't in this workspace")
    if look_id and cl.finished_picture(db, c, look_id) is None:
        raise HTTPException(422, "That look isn't this character's, or its picture isn't ready yet")
    if voices.find_voice(db, cur.workspace_id, c.voice_id) is None:
        raise HTTPException(422, f"{c.name} has no voice yet: give them one on their Voice tab first")
    cl.reference(db, c)  # 409 when there's no portrait to draw from
    parts = split_script(script)
    if not parts:
        raise HTTPException(422, "Write the script first")
    scenes = [new_scene(t) for t in parts]
    check_script(scenes, cur)
    pv = PresenterVideo(workspace_id=cur.workspace_id, character_id=c.id, look_id=look_id or None,
                        title=(title or "").strip()[:200] or f"{c.name} presents", language=language or "en",
                        settings={**DEFAULT_SETTINGS, **{k: v for k, v in settings.items() if v is not None},
                                  "lower_name": settings.get("lower_name") or c.name},
                        scenes=scenes, stages={k: {"status": "pending"} for k in STAGE_KEYS}, outputs={},
                        created_by=cur.id)
    db.add(pv)
    db.flush()
    return pv


def ensure_idle(db: Session, pv: PresenterVideo) -> None:
    job = db.get(Job, pv.job_id or "")
    if job is not None and job.status in ("queued", "running"):
        raise HTTPException(409, "This presenter video is already being made; wait for it or cancel it")


def queue(db: Session, pv: PresenterVideo, run: str = "continue", quality: str = "draft") -> Job:
    ensure_idle(db, pv)
    keys = ("speech", "frames") if run == "continue" else STAGE_KEYS
    stages = dict(pv.stages or {})
    for k in keys:
        if (stages.get(k) or {}).get("status") != "review":
            stages[k] = {"status": "pending"}
    pv.stages = stages
    job = Job(workspace_id=pv.workspace_id, type=JOB, message="Waiting for a worker · Presenter video",
              payload={"presenter_id": pv.id, "run": run, "quality": quality if quality in QUALITIES else "draft"})
    db.add(job)
    db.flush()
    pv.job_id = job.id
    return job


def scene_out(db: Session, sc: dict) -> dict:
    from app.services import gen_out

    def g(gid):
        x = db.get(Generation, gid or "")
        return gen_out(x).model_dump() if x is not None else None

    return {"id": sc["id"], "text": sc["text"], "background": sc.get("background") or "",
            "duration_s": sc.get("duration_s"), "speech": g((sc.get("speech") or {}).get("gen")),
            "frame": g((sc.get("frame") or {}).get("gen")),
            "take": {q: g((t or {}).get("gen")) for q, t in (sc.get("take") or {}).items()}}


def presenter_out(db: Session, pv: PresenterVideo) -> dict:
    from app.services import job_out

    c = db.get(Character, pv.character_id or "")
    job = db.get(Job, pv.job_id or "")
    return {"id": pv.id, "title": pv.title, "language": pv.language, "character_id": pv.character_id,
            "character_name": c.name if c else None, "look_id": pv.look_id, "settings": settings_of(pv),
            "scenes": [scene_out(db, s) for s in pv.scenes or []],
            "stages": [{"key": k, "label": lbl, **((pv.stages or {}).get(k) or {"status": "pending"})}
                       for k, lbl in STAGES],
            "outputs": pv.outputs or {}, "job": job_out(job) if job is not None else None,
            "created_at": pv.created_at, "updated_at": pv.updated_at}
