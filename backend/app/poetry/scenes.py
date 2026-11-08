"""A Short's scenes (contract v33): the LLM's prompts, and the keyframe and clip requests built from them.

Keyframes come from the chosen image model, or from the edit model with a Character Studio sheet panel as
the reference when a character is picked (the same panel choice a storyboard shot makes). Clips are LTX-2.3
image-to-video at the scene's length; past one pass they are long takes.
"""
import logging

from app import library as lib
from app import longtake
from app import mentions as mn
from app import models_catalog as mc
from app import rights
from app import montage as mt
from app import storyboard as sb
from app.drivers.comfy import HQ_SIZES, VIDEO_SIZES
from app.llm import LLMError, chat_sync
from app.llm import prompts as P
from app.models import Character, Generation, PoetryShort, Shot
from app.poetry import service as svc

log = logging.getLogger("mixai.poetry")

LOOK = ("Cinematic film still, anamorphic lens, soft volumetric light, rich warm colour, fine film grain, "
        "shallow depth of field.")
NEGATIVE = ("text, letters, captions, subtitles, watermark, logo, signature, extra people, crowd, deformed hands, "
            "fast motion, jump cut")
DEFAULT_PERSON = "a lone woman in a flowing dusty-rose hooded robe, seen from behind"
DEFAULT_SETTING = "an old walled city of sandstone arches and narrow lanes"
DEFAULT_MOTION = "She walks slowly away from the camera; the hem of her robe drifts into dust; slow push-in."


def character_of(db, ps: PoetryShort) -> Character | None:
    cid = svc.settings_of(ps).get("character_id")
    c = db.get(Character, cid) if cid else None
    return c if c is not None and c.workspace_id == ps.workspace_id else None


def person(db, ps: PoetryShort) -> str:
    st = svc.settings_of(ps)
    c = character_of(db, ps)
    parts = [x for x in ((c.description or c.name) if c else "", st.get("character") or "") if x]
    return " ".join(" ".join(parts).split())[:600]


def plan(db, ps: PoetryShort, tick=None) -> list[dict]:
    """Scenes cut on line starts, each with an image and a motion prompt that follow the poem."""
    st = svc.settings_of(ps)
    firsts = svc.group_scenes([float(ln["start"]) for ln in ps.lines], svc.song_duration(ps) or 0.0)
    scenes = [svc.new_scene(n, f) for n, f in enumerate(firsts, 1)]
    ps.scenes = scenes
    groups = [svc.scene_lines(ps, k) for k in range(len(scenes))]
    who, where = person(db, ps), st.get("setting") or ""
    try:
        res = chat_sync("creative", P.poetry_scene_messages(ps.title, ps.language, groups, who, where,
                                                            st.get("mood") or "", st["aspect"]),
                        schema=P.PoetryScenes, temperature=0.7, max_tokens=3500, tick=tick)
        drafts, who_llm, where_llm = res.data.scenes, res.data.character, res.data.setting
    except LLMError as e:
        log.warning("poetry scene prompts fell back to the template: %s", e)
        drafts, who_llm, where_llm = [], "", ""
    who = who or " ".join(who_llm.split()) or DEFAULT_PERSON
    where = where or " ".join(where_llm.split()) or DEFAULT_SETTING
    for k, sc in enumerate(scenes):
        d = drafts[k] if k < len(drafts) else None
        image = " ".join(d.image.split()) if d else f"{who}, in {where}, scene {k + 1} of {len(scenes)}."
        sc["image_prompt"] = image[:1500]
        sc["motion_prompt"] = (" ".join(d.motion.split()) if d and d.motion.strip() else DEFAULT_MOTION)[:600]
    ps.scenes = scenes
    ps.settings = {**(ps.settings or {}), "person": who[:600], "place": where[:600]}
    return scenes


def _ref(db, ps: PoetryShort, sc: dict) -> tuple[Generation | None, str]:
    c = character_of(db, ps)
    if c is None:
        return None, ""
    look = svc.settings_of(ps).get("look_id")
    if look:
        from app.character_looks import finished_picture

        g = finished_picture(db, c, look)
        if g is not None:
            return g, c.name
    # a throwaway shot so the storyboard's panel choice applies (from behind -> the back turn, and so on)
    shot = Shot(description=sc.get("image_prompt") or "", shot_type="wide", camera_rack={}, ref_panels={})
    return sb.character_ref(db, shot, c)["generation"], c.name


def keyframe_request(db, ps: PoetryShort, sc: dict) -> tuple[str, dict]:
    st = svc.settings_of(ps)
    w, h = lib.size_for_aspect(st["aspect"])
    who, where = st.get("person") or "", st.get("place") or ""
    body = sc["image_prompt"]
    # the person and the place ride on every frame so the scenes stay one film
    anchor = " ".join(x for x in (f"The person: {who}." if who else "", f"The setting: {where}." if where else "") if x)
    params = {"aspect": st["aspect"], "width": w, "height": h, "negative": NEGATIVE, "user_prompt": body,
              "poetry": ps.id, "scene": sc["id"], "created_by": {"user_id": ps.created_by, "flow": "poetry_short"}}
    ref, name = _ref(db, ps, sc)
    if ref is not None:
        m = mc.default_for("edit")
        params.update(reference_ids=[ref.id], reference_labels=[name], model=m.id)
        prompt = (mn.ref_preamble([name]) + "Compose a new film frame with this person: keep the face, hair and "
                  f"clothing exactly. {body} {anchor} {LOOK}")
    else:
        model_id = st.get("image_model") or svc.DEFAULTS["image_model"]
        m, chosen = rights.choose(db, model_id, "image", body)
        params.update(model=m.id, **chosen)
        prompt = f"{body} {anchor} {LOOK}"
    return " ".join(prompt.split()), params


def clip_seconds(ps: PoetryShort, k: int) -> float:
    wins = svc.scene_windows(ps)
    a, b = wins[k]
    # every clip but the last runs a cross-fade longer (montage.slot_frames)
    return round(b - a + (mt.XFADE if k + 1 < len(wins) else 0.0), 2)


def clip_request(db, ps: PoetryShort, sc: dict, k: int, keyframe: Generation) -> tuple[str, dict]:
    st = svc.settings_of(ps)
    seconds = clip_seconds(ps, k)
    long = longtake.is_long(seconds)
    # High quality is one pass only; a longer scene renders in Standard
    m = mc.get("ltx23_hq" if st["quality"] == "final" and not long else "ltx23_distilled", "video")
    w, h = (HQ_SIZES if m.id == "ltx23_hq" else VIDEO_SIZES)[st["aspect"]]
    params = {"model": m.id, "aspect_ratio": st["aspect"], "duration_s": seconds, "fps": longtake.FPS,
              "num_frames": mc.video_frames(m, seconds, longtake.FPS), "width": w, "height": h,
              "first_frame_id": keyframe.id, "smooth_motion": False, "negative": NEGATIVE,
              "user_prompt": sc["motion_prompt"], "poetry": ps.id, "scene": sc["id"],
              "created_by": {"user_id": ps.created_by, "flow": "poetry_short"}}
    if m.id == "ltx23_hq":
        params["quality"] = "hq"
    if long:
        longtake.init_params(params)
    # the song is the soundtrack; nobody on screen sings, so nothing for the model to voice
    prompt = f"{sc['motion_prompt']} {sc['image_prompt']} Quiet ambience only, no speech, no singing."
    return " ".join(prompt.split()), params
