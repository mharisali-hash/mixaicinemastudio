"""Real generation driver: maps a Generation onto one of our ComfyUI templates."""
import asyncio
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import Settings, get_settings
from app.drivers.base import DriverResult, ProgressCallback
from app.drivers.comfy_client import ComfyClient, ComfyError, ComfyOutput, parse_outputs, pick_client
from app import aerab, gpu_memory
from app import models_catalog as mc
from app import native_audio
from app.character_panels import VIEW_ALIASES, VIEW_PROMPTS
from app.lanes import AUDIO_KINDS
from app.workflows import batch_latent, build, frames_for, load, snap_multiple

log = logging.getLogger("mixai.comfy")

# keyframes: ~0.6-0.9 MP, multiples of 16 for Z-Image / Qwen latents
IMAGE_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024), "4:3": (1152, 864), "2.39:1": (1536, 640)}
# LTX draft sizes, multiples of 32. Final-res passes go through the x2 upscaler later.
VIDEO_SIZES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640), "4:3": (736, 544), "2.39:1": (960, 416)}
# High quality LTX: final size (multiples of 64, so stage 1 at half size stays on the 32 grid)
HQ_SIZES = {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960), "4:3": (1088, 832), "2.39:1": (1536, 640)}
WAN_SIZES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (624, 624), "4:3": (704, 544), "2.39:1": (960, 400)}
PORTRAIT_SIZE = (768, 1024)
# Wan 2.2 S2V: its 480p sizes (a 14B model at 1280x704 doesn't fit the 20 GB cards), 77 frames a pass at 16 fps
WAN_S2V_SIZES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640), "4:3": (720, 544), "2.39:1": (960, 400)}
# S2V is trained at 480p and 720p; hq takes go straight to 720p instead of being stretched from 480p
WAN_S2V_HQ_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960), "4:3": (1088, 816),
                    "2.39:1": (1536, 640)}
# fast: Comfy's template with the lightx2v T2V LoRA, which Comfy itself says costs S2V detail and motion;
# full: no LoRA, at Wan's own S2V shift and guide scale (3, 4.5) and 20 of its 40 steps
WAN_S2V_FINISH = {"fast": {"steps": 4, "cfg": 1.0, "shift": 8.0, "full_model": False},
                  "full": {"steps": 20, "cfg": 4.5, "shift": 3.0, "full_model": True}}
WAN_S2V_FPS, WAN_S2V_PASS, WAN_S2V_MAX_PASSES = 16.0, 77, 3
IDEOGRAM_MIN_PIXELS = 1024 * 1024
MAX_FRAMES = 257  # ~10.7 s at 24 fps; longer shots belong to the long-take templates

ANGLES_LORA = "qwen-image-edit-2511-multiple-angles-lora.safetensors"

TIMEOUTS = {"zimage_t2i": 900.0, "qwen_edit": 900.0, "ltx23_i2v": 3600.0, "ltx23_extend": 3600.0,
            "upscale_seedvr2": 3600.0, "upscale_flashvsr": 3600.0, "upscale_esrgan": 1800.0,
            "photo_model_1x": 900.0, "photo_face_restore": 600.0, "photo_colourise": 600.0, "photo_supir": 1800.0,
            "photo_cutout_mask": 300.0, "photo_sam_mask": 300.0,
            "image_upscale_zimage": 900.0, "image_upscale_seedvr2": 900.0, "image_upscale_esrgan": 300.0,
            "qwen_image_t2i": 1200.0, "flux2_klein_t2i": 600.0, "flux2_klein_edit": 900.0,
            "ltx23_two_stage": 3600.0, "ltx23_ia2v": 3600.0, "ltx23_ia2v_extend": 3600.0, "wan22_t2v": 2400.0, "wan22_s2v": 10800.0,
            "zimage_i2i": 900.0, "flux2_klein_i2i": 600.0, "qwen_image_i2i": 1200.0,
            "ace15_song": 900.0, "sa3_music": 600.0, "sa3_sfx": 600.0, "sa3_medium": 1200.0, "minimax_music3": 1800.0,
            "image_lineart": 300.0, "image_lineart_anime": 300.0, "image_depth": 300.0, "image_canny": 120.0,
            "image_softedge": 300.0, "image_lama_inpaint": 600.0, "image_remove_words": 900.0,
            "image_iclight": 900.0, "image_catvton": 1200.0,
            "zimage_controlnet": 900.0, "zimage_controlnet_depth": 900.0, "zimage_controlnet_canny": 900.0,
            "zimage_controlnet_pose": 900.0, "zimage_controlnet_hed": 900.0, "zimage_controlnet_scribble": 900.0,
            # contract v32: first loads of 9-16 GB weights on the 20 GB card are slow
            "krea2_t2i": 1200.0, "krea2_raw_t2i": 2400.0, "hidream_o1_dev_t2i": 1800.0, "hidream_o1_t2i": 2400.0,
            "anima_t2i": 600.0, "anima_i2i": 600.0, "illustrious_xl_t2i": 600.0, "illustrious_xl_i2i": 600.0,
            "realvis_xl_t2i": 600.0, "realvis_xl_i2i": 600.0, "flux2_klein9b_t2i": 900.0,
            "flux2_klein9b_i2i": 900.0, "flux2_klein9b_edit": 900.0, "qwen_image21_t2i": 1800.0,
            "qwen_image21_full_t2i": 2400.0, "ernie_turbo_t2i": 1200.0, "zimage_base_t2i": 1200.0,
            "zimage_base_i2i": 1200.0, "chroma1_hd_t2i": 1800.0, "wan22_still_t2i": 1800.0,
            "ideogram4_t2i": 2400.0,
            # contract v40: a Final model is four samplers plus remesh / bake at 4K; the first run loads ~9 GB
            "trellis2_image_to_3d": 3600.0}
# templates whose LoRA insertion point takes the user's LoRAs (Z-Image / Qwen-Edit / LTX families)
LORA_FAMILIES = {"zimage_t2i", "zimage_i2i", "qwen_edit", "ltx23_i2v", "ltx23_extend", "ltx23_ia2v",
                 "ltx23_ia2v_extend"}
RESTORE_TEMPLATES = {"photo_model_1x", "photo_face_restore", "photo_colourise", "photo_supir", "photo_cutout_mask",
                     "photo_sam_mask", "image_upscale_seedvr2"}
# graphs where LTX writes the audio itself (no speech track)
NATIVE_AUDIO = {"ltx23_i2v", "ltx23_two_stage", "ltx23_extend"}
IMAGE_UPSCALE_TEMPLATES = {"image_upscale_zimage", "image_upscale_seedvr2", "image_upscale_esrgan"}
UPSCALE_TEMPLATES = {"upscale_seedvr2", "upscale_flashvsr", "upscale_esrgan"}

# TextEncodeAceStepAudio1.5's language list (comfy_extras/nodes_ace.py); anything else goes in as "unknown"
ACE_LANGUAGES = frozenset({
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fr", "he", "hi", "hr", "ht", "hu",
    "id", "is", "it", "ja", "ko", "la", "lt", "ms", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "sa", "sk", "sr",
    "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi", "yue", "zh"})
# our languages ACE-Step has no tag for: the nearest one it was trained on (Shahmukhi is still Punjabi; Sindhi
# and Pashto sung in Pakistan sit closer to Urdu than to anything else on the list)
ACE_NEAREST = {"ur-latn": "ur", "pnb": "pa", "sd": "ur", "ps": "ur"}
VOCALS = {"male": "male vocals", "female": "female vocals", "duet": "male and female duet vocals"}
SA3_HINTS = {"loop": "seamless loop", "oneshot": "one-shot", "instrument": "solo instrument"}


@dataclass
class Plan:
    template: str
    inputs: dict[str, Any]
    images: dict[str, Path | list[Path]] = field(default_factory=dict)
    loras: list[tuple[str, float]] = field(default_factory=list)
    media: str = "image"
    sources: dict[str, Any] = field(default_factory=dict)  # generation ids behind the images, for the record
    # (latent batch size, first index, how many): see workflows.batch_latent
    batch: tuple[int, int, int] | None = None


class DbLookup:
    """Reads the few things the driver needs from the app DB (own short-lived session)."""

    def _session(self):
        from app.db import SessionLocal

        return SessionLocal()

    def generation_file(self, gen_id: str) -> Path:
        from app.models import Generation
        from app.services import generation_file

        with self._session() as db:
            g = db.get(Generation, gen_id)
            if g is None:
                # usually the picture was deleted while this job waited in the queue
                raise ComfyError(f"A picture this needs was deleted before it could be used ({gen_id[:8]}). "
                                 "Pick another picture and try again")
            if g.status not in ("ready", "approved") or not g.file_path:
                raise ComfyError(f"referenced generation {gen_id} has no finished image (status {g.status})")
            path = generation_file(g)
        if not path or not path.is_file():
            raise ComfyError(f"file for generation {gen_id} is missing on disk")
        return path

    def character_portrait(self, character_id: str) -> str | None:
        from sqlalchemy import select

        from app.models import Generation

        with self._session() as db:
            base = select(Generation.id).where(
                Generation.target_type == "character", Generation.target_id == character_id,
                Generation.kind == "portrait", Generation.file_path.is_not(None),
            )
            approved = db.scalars(base.where(Generation.status == "approved")).first()
            if approved:
                return approved
            # no approved portrait yet: newest finished one is better than refusing outright
            return db.scalars(base.where(Generation.status == "ready").order_by(Generation.created_at.desc())).first()

    def project_aspect(self, project_id: str | None) -> str | None:
        if not project_id:
            return None
        from app.models import Project

        with self._session() as db:
            p = db.get(Project, project_id)
            return p.aspect_ratio if p else None


def _ids(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def _size(params: dict, default: tuple[int, int], multiple: int) -> tuple[int, int]:
    w = params.get("width") or default[0]
    h = params.get("height") or default[1]
    return snap_multiple(w, multiple), snap_multiple(h, multiple)


def _loras(params: dict) -> list[tuple[str, float]]:
    # TODO: check names (and model family) against the lora table / object_info before queueing,
    # so a typo fails here instead of as a ComfyUI validation error
    out = []
    for item in params.get("loras") or []:
        if isinstance(item, str):
            out.append((item, 1.0))
        elif isinstance(item, dict) and item.get("name"):
            out.append((item["name"], float(item.get("strength", 1.0))))
    return out


def _ref_preamble(labels, n: int) -> str:
    # the Qwen edit encoder names its inputs "Picture 1..3"; say which is which so it composes
    # rather than copying picture 1 back out
    labels = list(labels or [])[:n]
    if not labels:
        return ""
    parts = [f"Picture {i} shows {name}" for i, name in enumerate(labels, 1)]
    return ("; ".join(parts) + ". Compose a new film frame with them: keep each character's face, hair and "
            "clothing exactly, and match the location. ")


def plan_generation(kind: str, prompt: str, params: dict, seed: int, lookup) -> Plan:
    plan = _plan(kind, prompt, params or {}, seed, lookup)
    if plan.template in NATIVE_AUDIO and "prompt" in plan.inputs:
        # LTX makes up its own soundtrack here; tell it what (not) to say. The lip-synced graphs get the voice.
        plan.inputs["prompt"] = native_audio.ltx_prompt(plan.inputs["prompt"])
    return plan


def _plan(kind: str, prompt: str, params: dict, seed: int, lookup) -> Plan:
    negative = params.get("negative") or params.get("negative_prompt") or ""
    ref_ids = _ids(params.get("reference_ids"))[:3]
    loras = _loras(params)

    if kind == "model3d":
        return model3d_plan(params.get("model3d") or {}, seed, lookup)

    if params.get("restore"):
        return restore_plan(params["restore"], seed, lookup)

    if params.get("image_tool"):
        return image_tool_plan(params["image_tool"], seed, lookup)

    if params.get("control"):
        return control_plan(params["control"], prompt, negative, seed, lookup)

    if params.get("upscale") and kind in ("portrait", "sheet_view", "establishing", "keyframe_start", "keyframe_end",
                                          "keyframe_mid", "image"):
        return image_upscale_plan(params["upscale"], negative, seed, lookup)

    def edit_plan(ids: list[str], text: str, size: tuple[int, int], model: str | None = None) -> Plan:
        m = mc.MODELS.get(model or edit_model_id(params)) or mc.default_for("edit")
        ids = ids[: m.max_refs or 3]
        files = [lookup.generation_file(i) for i in ids]
        w, h = _size(params, size, 16)
        inputs = {"prompt": text, "negative": negative, "width": w, "height": h, "seed": seed}
        if params.get("steps"):
            inputs["steps"] = params["steps"]
        return Plan(m.template, inputs, {"images": files}, _family(m.template, loras), "image",
                    {"reference_ids": ids, "model": m.id})

    def t2i_plan(size: tuple[int, int]) -> Plan:
        m = mc.MODELS.get(params.get("model") or "")
        if m is None or m.type != "image":
            m = mc.default_for("image")
        w, h = _size(params, size, 16)
        inputs = {"prompt": prompt, "negative": negative, "width": w, "height": h, "seed": seed}
        sources = {"model": m.id}
        sp = next((x for x in m.speeds if x.id == params.get("speed")), None) or \
            next((x for x in m.speeds if x.id == m.default_speed), None)
        if sp:
            inputs.update(steps=sp.steps, cfg=sp.cfg, speed_lora=sp.lora)
            if sp.shift is not None:
                inputs["shift"] = sp.shift
            if sp.sampler:
                inputs.update(sampler=sp.sampler, scheduler=sp.scheduler)
            inputs.update(sp.extra)
            sources["speed"] = sp.id
        if params.get("steps"):
            inputs["steps"] = params["steps"]
        if m.id == "ideogram4" and w * h < IDEOGRAM_MIN_PIXELS:
            # under Ideogram 4's smallest official size; its own site serves 1024x1024 with clean lettering, so
            # the pickers' ~1 MP is enough and only smaller requests are raised
            k = math.sqrt(IDEOGRAM_MIN_PIXELS / (w * h))
            w, h = min(2048, snap_multiple(w * k, 16)), min(2048, snap_multiple(h * k, 16))
            inputs.update(width=w, height=h)
        if m.id == "ideogram4" and params.get("ideogram_caption"):
            # the JSON caption the worker wrote (app.ideogram_caption); a pasted caption is already in prompt
            inputs["prompt"] = params["ideogram_caption"]
        tpl = mc.template_for(m, sp.id if sp else None)
        return Plan(tpl, inputs, {}, _family(tpl, loras), "image", sources)

    def i2i_plan(size: tuple[int, int]) -> Plan:
        # the t2i recipe (model, speed, size) on the model's image-to-image graph
        plan = t2i_plan(size)
        m = mc.MODELS[plan.sources["model"]]
        tpl = m.extra.get("i2i")
        if not tpl:
            raise ComfyError(f"{m.label} can't redraw an existing picture")
        src = params["img2img_of"]
        strength = min(0.9, max(0.1, float(src.get("strength") or 0.45)))
        plan.template = tpl
        plan.inputs["denoise"] = strength
        if tpl in ("flux2_klein_i2i", "flux2_klein9b_i2i"):
            # SplitSigmasDenoise keeps round(steps * strength) steps; stretch so ~4 real steps run
            plan.inputs["steps"] = max(4, math.ceil(int(plan.inputs.get("steps") or 4) / strength))
        plan.images = {"image": lookup.generation_file(src["generation_id"])}
        plan.loras = _family(tpl, loras)
        plan.sources.update(img2img_of=src["generation_id"], strength=strength)
        return plan

    if kind == "image":
        # Image studio (contract v5): sources make it an edit, otherwise plain text to image.
        # The API already picked a ~1 MP size for the aspect.
        size = (params.get("width") or 1024, params.get("height") or 1024)
        if params.get("img2img_of"):
            return i2i_plan(size)
        if ref_ids:
            return edit_plan(ref_ids, prompt, size)
        plan = t2i_plan(size)
        n = 1 + len(params.get("batch_out") or [])
        at = int(params.get("batch_index") or 0)
        if n > 1:
            plan.batch = (n, 0, n)
        elif at > 0:
            # one picture of an earlier batch, drawn again on its own
            plan.batch = (at + 1, at, 1)
        return plan

    if kind == "video" and params.get("speech_audio"):
        # a Character Studio test clip: a Library "video" with a voice (contract v26), lip-synced like a take
        return lipsync_plan(prompt, params, seed, lookup, negative, loras)

    if kind == "video":
        return clip_plan(prompt, params, seed, lookup, negative, loras)

    if kind == "portrait":
        return edit_plan(ref_ids, prompt, PORTRAIT_SIZE) if ref_ids else t2i_plan(PORTRAIT_SIZE)

    if kind == "sheet_view" and params.get("object"):
        # a sheet's prop or accessory: an object on its own, so a product shot from text, not an edit
        return t2i_plan((1024, 1024))

    if kind == "sheet_view":
        ids = ref_ids
        if not ids:
            portrait = lookup.character_portrait(params["target_id"]) if params.get("target_id") else None
            if not portrait:
                raise ComfyError("This character has no portrait yet. Generate (and ideally approve) one first.")
            ids = [portrait]
        view = str(params.get("view") or "front").lower().replace(" ", "_")
        view = VIEW_ALIASES.get(view, view)
        if params.get("panel"):
            # a character sheet panel (app.character_sheet): its prompt is already the whole edit instruction
            text = prompt.strip()
        else:
            angle = VIEW_PROMPTS.get(view, view.replace("_", " "))
            text = (
                f"Show the same character from a new camera angle: {angle}. "
                "Keep the face, hairstyle, body shape, clothing and colours exactly the same. "
                "Plain light grey studio background, soft even lighting, character sheet style."
            )
            if prompt.strip():
                text += f" {prompt.strip()}"
        plan = edit_plan(ids, text, PORTRAIT_SIZE, model="qwen_image_edit_2511")  # the angles LoRA is Qwen-only
        if params.get("angles_lora"):
            strength = params["angles_lora"] if isinstance(params["angles_lora"], (int, float)) else 1.0
            plan.loras = [(ANGLES_LORA, float(strength))] + plan.loras
        plan.inputs["view"] = view  # recorded only; build() ignores unknown keys
        return plan

    if kind in ("keyframe_start", "keyframe_end", "keyframe_mid", "establishing"):
        aspect = params.get("aspect_ratio") or lookup.project_aspect(params.get("project_id")) or "16:9"
        size = IMAGE_SIZES.get(aspect, IMAGE_SIZES["16:9"])
        if not ref_ids:
            return t2i_plan(size)
        # the preamble talks Qwen's "Picture N" language; FLUX.2 just gets the prompt
        qwen = edit_model_id(params) == "qwen_image_edit_2511"
        pre = _ref_preamble(params.get("reference_labels"), len(ref_ids)) if qwen else ""
        return edit_plan(ref_ids, pre + prompt, size)

    if kind == "take" and params.get("speech_audio"):
        return lipsync_plan(prompt, params, seed, lookup, negative, loras)

    if kind == "take":
        first = params.get("first_frame_id")
        if not first:
            raise ComfyError("A take needs params.first_frame_id (the START keyframe generation)")
        last = params.get("last_frame_id")
        aspect = params.get("aspect_ratio") or lookup.project_aspect(params.get("project_id")) or "16:9"
        w, h = _size(params, VIDEO_SIZES.get(aspect, VIDEO_SIZES["16:9"]), 32)
        fps = float(params.get("fps") or 24)
        frames = int(params["num_frames"]) if params.get("num_frames") else frames_for(float(params.get("duration_s") or 5), fps)
        frames = min(frames, MAX_FRAMES)
        images: dict[str, Path | list[Path]] = {"first_image": lookup.generation_file(first)}
        if last:
            images["last_image"] = lookup.generation_file(last)
        inputs = {"prompt": prompt, "width": w, "height": h, "num_frames": frames, "fps": fps, "seed": seed}
        if negative:
            inputs["negative"] = negative
        sources = {"first_frame_id": first, "last_frame_id": last}
        if params.get("quality") == "hq":
            return hq_plan(inputs, images, aspect, params, loras, sources)
        if params.get("smooth_motion"):
            inputs["smooth_fps"] = fps * 2
            sources["smooth_motion"] = True
        return Plan("ltx23_i2v", inputs, images, loras, "video", sources)

    if kind == "take_chunk" and params.get("speech_audio"):
        # a piece of a continuous camera track: the first starts from the angle keyframe, the rest continue
        if params.get("context_video"):
            return lipsync_extend_plan(prompt, params, seed, lookup, negative, loras)
        # always LTX: the pieces are planned in 24 fps frames and continue from each other on LTX's extend
        # graph. A Wan first piece (16 fps, 150 of 225 frames) slid every later piece off its voice (24 Oct)
        return lipsync_plan(prompt, {**params, "lipsync_engine": "ltx"}, seed, lookup, negative, loras)

    if kind == "take_chunk":
        return chunk_plan(prompt, params, seed, lookup, negative, loras)

    if kind == "upscale_segment":
        return upscale_plan(params, seed)

    if kind in AUDIO_KINDS:
        return audio_plan(kind, prompt, params, seed, lookup)

    raise ComfyError(f"The ComfyUI driver doesn't handle '{kind}' generations")


def edit_model_id(params: dict) -> str:
    m = mc.MODELS.get(params.get("edit_model") or "") or mc.MODELS.get(params.get("model") or "")
    return m.id if m is not None and m.type == "edit" else mc.default_for("edit").id


def _family(template: str, loras):
    # a Z-Image or LTX LoRA would break a Qwen-Image / FLUX.2 / Wan graph
    return list(loras) if template in LORA_FAMILIES else []


def hq_plan(inputs: dict, images: dict, aspect: str, params: dict, loras, sources: dict) -> Plan:
    """LTX two-stage: same inputs as a standard take, at HQ size (stage 1 runs at half of it)."""
    w, h = HQ_SIZES.get(aspect, HQ_SIZES["16:9"])
    inputs = {**inputs, "width": w, "height": h, "half_width": w // 2, "half_height": h // 2}
    sources = {**sources, "quality": "hq"}
    if params.get("smooth_motion"):
        inputs["smooth_fps"] = float(inputs["fps"]) * 2
        sources["smooth_motion"] = True
    return Plan("ltx23_two_stage", inputs, images, _family("ltx23_i2v", loras), "video", sources)


def _lipsync_engine(params: dict) -> str:
    # the film's choice (film_audio.attach), else the server default
    return params.get("lipsync_engine") or get_settings().film_lipsync_engine


def wan_s2v_plan(prompt: str, params: dict, seed: int, lookup) -> Plan:
    """A lip-synced take on Wan 2.2 S2V: the angle keyframe plus the speaker's voice, 77-frame passes at 16 fps
    (one to three) trimmed to the take. LTX's audio-to-video often left the mouth still while the voice ran
    (Ruslan's explainer, 23 Oct); S2V is built for a talking presenter."""
    first = params.get("first_frame_id")
    aspect = params.get("aspect_ratio") or lookup.project_aspect(params.get("project_id")) or "16:9"
    sizes = WAN_S2V_HQ_SIZES if params.get("quality") == "hq" else WAN_S2V_SIZES
    w, h = sizes.get(aspect, sizes["16:9"])
    finish = params.get("s2v_finish") or get_settings().wan_s2v_finish
    dur = float(params.get("duration_s") or 5)
    frames = max(1, round(dur * WAN_S2V_FPS))
    passes = min(WAN_S2V_MAX_PASSES, math.ceil(frames / WAN_S2V_PASS))
    frames = min(frames, passes * WAN_S2V_PASS)
    inputs: dict[str, Any] = {
        "prompt": prompt, "width": w, "height": h, "frames": frames, "passes": passes, "seed": seed,
        "audio_start_s": round(float(params.get("audio_start_s") or 0.0), 4), "duration_s": round(frames / WAN_S2V_FPS, 4),
        "one_pass": passes == 1, "two_passes": passes == 2,
        **WAN_S2V_FINISH.get(finish, WAN_S2V_FINISH["fast"]),
    }
    images: dict[str, Path | list[Path]] = {"first_image": lookup.generation_file(first),
                                            "speech_audio": Path(params["speech_audio"])}
    sources = {"first_frame_id": first, "lipsync": True, "engine": "wan_s2v", "passes": passes,
               "finish": finish if finish in WAN_S2V_FINISH else "fast"}
    return Plan("wan22_s2v", inputs, images, [], "video", sources)


def _modality() -> dict:
    # LTX's audio/video sync guidance on the first pass; at cfg 1 and no modality term the mouth often stayed
    # still while the voice ran. 0 (or anything up to 1, which does nothing) drops the node
    scale = float(get_settings().ltx_modality_scale or 0)
    return {"modality_scale": scale} if scale > 1 else {"no_modality": True}


def lipsync_plan(prompt: str, params: dict, seed: int, lookup, negative: str, loras) -> Plan:
    """A lip-synced take (AI video podcast): START frame plus the speaker's dry voice, cut by the graph's
    TrimAudioDuration to [audio_start_s, +duration]. Draft is one pass at the draft size; "hq" is the two-pass
    graph at HQ size, its first pass at half."""
    first = params.get("first_frame_id")
    if not first:
        raise ComfyError("A lip-sync take needs params.first_frame_id (the angle keyframe)")
    if _lipsync_engine(params) == "wan_s2v":
        return wan_s2v_plan(prompt, params, seed, lookup)
    aspect = params.get("aspect_ratio") or lookup.project_aspect(params.get("project_id")) or "16:9"
    final = params.get("quality") == "hq"
    w, h = (HQ_SIZES if final else VIDEO_SIZES).get(aspect, (HQ_SIZES if final else VIDEO_SIZES)["16:9"])
    fps = float(params.get("fps") or 24)
    frames = int(params["num_frames"]) if params.get("num_frames") else frames_for(float(params.get("duration_s") or 5), fps)
    frames = min(frames, MAX_FRAMES)
    inputs: dict[str, Any] = {
        "prompt": prompt, "width": w, "height": h, "latent_width": w // 2 if final else w,
        "latent_height": h // 2 if final else h, "num_frames": frames, "fps": fps, "seed": seed,
        # the template feeds duration*fps+1 frames from the same duration, so the audio matches frames-1
        "audio_start_s": round(float(params.get("audio_start_s") or 0.0), 4), "duration_s": round((frames - 1) / fps, 4),
        "single_stage": not final, **_modality(),
    }
    if negative:
        inputs["negative"] = negative
    images: dict[str, Path | list[Path]] = {"first_image": lookup.generation_file(first),
                                            "speech_audio": Path(params["speech_audio"])}
    sources = {"first_frame_id": first, "lipsync": True, "quality": "hq" if final else "draft"}
    return Plan("ltx23_ia2v", inputs, images, _family("ltx23_ia2v", loras), "video", sources)


def lipsync_extend_plan(prompt: str, params: dict, seed: int, lookup, negative: str, loras) -> Plan:
    """A later piece of a continuous camera track: the previous piece's tail as context frames, the track
    audio for the whole window frozen (audio_start_s is where the window starts, context included), and
    the angle keyframe as a light guide on the last frame."""
    aspect = params.get("aspect_ratio") or lookup.project_aspect(params.get("project_id")) or "16:9"
    final = params.get("quality") == "hq"
    w, h = (HQ_SIZES if final else VIDEO_SIZES).get(aspect, (HQ_SIZES if final else VIDEO_SIZES)["16:9"])
    fps = float(params.get("fps") or 24)
    frames = min(int(params["num_frames"]), MAX_FRAMES)
    inputs: dict[str, Any] = {
        "prompt": prompt, "width": w, "height": h, "latent_width": w // 2 if final else w,
        "latent_height": h // 2 if final else h, "num_frames": frames, "fps": fps, "seed": seed,
        "audio_start_s": round(float(params.get("audio_start_s") or 0.0), 4), "duration_s": round((frames - 1) / fps, 4),
        "single_stage": not final, **_modality(),
    }
    if params.get("guide_strength") is not None:
        inputs["guide_strength"] = float(params["guide_strength"])
    if negative:
        inputs["negative"] = negative
    images: dict[str, Path | list[Path]] = {"context_video": Path(params["context_video"]),
                                            "speech_audio": Path(params["speech_audio"])}
    guide = params.get("guide_frame_id")
    if guide:
        images["guide_image"] = lookup.generation_file(guide)
    sources = {"chunk": params.get("chunk_idx"), "guide_frame_id": guide, "lipsync": True,
               "quality": "hq" if final else "draft"}
    return Plan("ltx23_ia2v_extend", inputs, images, _family("ltx23_ia2v_extend", loras), "video", sources)


def clip_plan(prompt: str, params: dict, seed: int, lookup, negative: str, loras) -> Plan:
    """Create Video (contract v6): one prompt, optional start image, any video model."""
    m = mc.MODELS.get(params.get("model") or "") or mc.default_for("video")
    aspect = params.get("aspect_ratio") or "16:9"
    first = params.get("first_frame_id")
    images: dict[str, Path | list[Path]] = {}
    if first:
        if "i2v" not in m.capabilities:
            raise ComfyError(f"{m.label} can't start from an image")
        images["first_image"] = lookup.generation_file(first)
    last = params.get("last_frame_id")
    if last:
        if "flf" not in m.capabilities:
            raise ComfyError(f"{m.label} can't end on a given image")
        images["last_image"] = lookup.generation_file(last)
    duration = float(params.get("duration_s") or 5)
    sources = {"model": m.id, "first_frame_id": first, "last_frame_id": last}
    if m.id == "wan22_t2v":
        w, h = WAN_SIZES.get(aspect, WAN_SIZES["16:9"])
        inputs = {"prompt": prompt, "width": w, "height": h, "num_frames": mc.wan_frames(duration),
                  "fps": mc.WAN_FPS, "seed": seed}
        if negative:
            inputs["negative"] = negative
        return Plan(m.template, inputs, images, [], "video", sources)
    fps = 24.0
    frames = min(frames_for(duration, fps), MAX_FRAMES)
    w, h = VIDEO_SIZES.get(aspect, VIDEO_SIZES["16:9"])
    inputs = {"prompt": prompt, "width": w, "height": h, "num_frames": frames, "fps": fps, "seed": seed}
    if negative:
        inputs["negative"] = negative
    if m.id == "ltx23_hq":
        return hq_plan(inputs, images, aspect, params, loras, sources)
    if params.get("smooth_motion"):
        inputs["smooth_fps"] = fps * 2
        sources["smooth_motion"] = True
    return Plan("ltx23_i2v", inputs, images, loras, "video", sources)


def chunk_plan(prompt: str, params: dict, seed: int, lookup, negative: str, loras) -> Plan:
    """One chunk of a long take (app.longtake). Chunk 0 is plain I2V from START; later chunks
    continue from the previous chunk's tail clip (extend) or restart from its last frame (i2v)."""
    w, h = _size(params, VIDEO_SIZES["16:9"], 32)
    fps = float(params.get("fps") or 24)
    frames = min(int(params["num_frames"]), MAX_FRAMES)
    inputs: dict[str, Any] = {"prompt": prompt, "width": w, "height": h, "num_frames": frames, "fps": fps, "seed": seed}
    if negative:
        inputs["negative"] = negative
    images: dict[str, Path | list[Path]] = {}
    last = params.get("last_frame_id")
    if last:
        images["last_image"] = lookup.generation_file(last)
    sources = {"chunk": params.get("chunk_idx"), "last_frame_id": last}

    if params.get("context_video"):
        ctx_frames = int(params.get("context_frames") or 9)
        images["context_video"] = Path(params["context_video"])
        # the audio mask works in seconds over this chunk only, not the whole take
        inputs.update(duration_s=frames / fps, context_s=ctx_frames / fps)
        return Plan("ltx23_extend", inputs, images, loras, "video", sources)

    if params.get("first_image_path"):
        images["first_image"] = Path(params["first_image_path"])
    elif params.get("first_frame_id"):
        images["first_image"] = lookup.generation_file(params["first_frame_id"])
        sources["first_frame_id"] = params["first_frame_id"]
    elif not params.get("text_start"):
        raise ComfyError("A take chunk needs the START frame, a context clip or a first image")
    # text_start: chunk 0 of a long Create Video clip without a start image is plain text to video
    return Plan("ltx23_i2v", inputs, images, loras, "video", sources)


def upscale_plan(params: dict, seed: int) -> Plan:
    """One segment of an upscale job (app.upscale): a short video-only clip in, frames out."""
    template = params.get("template")
    if template not in UPSCALE_TEMPLATES:
        raise ComfyError(f"unknown upscale template {template!r}")
    inputs = {k: params[k] for k in ("width", "height", "fps", "scale", "model", "model_version", "color_correction")
              if params.get(k) is not None}
    inputs["seed"] = seed
    return Plan(template, inputs, {"video": Path(params["video_path"])}, [], "video",
                {"segment": params.get("segment_idx"), "source_id": params.get("source_id")})


def image_upscale_plan(up: dict, negative: str, seed: int, lookup) -> Plan:
    """A new version of an image, made from an older one (app.upscale_image)."""
    template = up.get("template")
    if template not in IMAGE_UPSCALE_TEMPLATES:
        raise ComfyError(f"unknown image upscale template {template!r}")
    inputs: dict[str, Any] = {"width": up["width"], "height": up["height"], "seed": seed}
    if template == "image_upscale_zimage":
        # TODO: carry the source's Z-Image LoRAs over once LoRAs record which model family they're for
        inputs.update(prompt=up.get("prompt") or "", negative=negative, denoise=up.get("denoise") or 0.33,
                      pre_enlarge=True if up.get("pre_enlarge", True) else None)
    elif template in ("image_upscale_seedvr2", "image_upscale_esrgan") and up.get("model"):
        inputs["model"] = up["model"]
    src = lookup.generation_file(up["source_id"])
    return Plan(template, inputs, {"image": src}, [], "image", {"source_id": up["source_id"]})


def restore_plan(r: dict, seed: int, lookup) -> Plan:
    """Photo Studio restore / cut-out tools (app.photo.restore). finish() there does the full-size work after."""
    from app.photo.restore import comfy_input

    template = r.get("template")
    src = comfy_input(r, lookup.generation_file(r["source_id"]))
    inputs: dict[str, Any] = {k: v for k, v in (r.get("inputs") or {}).items() if k != "template"}
    inputs["seed"] = seed
    sources = {"source_id": r["source_id"], "tool": r.get("tool")}
    if template in {m.template for m in mc.MODELS.values() if m.type == "edit"}:
        inputs.setdefault("negative", "")
        return Plan(template, inputs, {"images": [src]}, [], "image", sources)
    if template not in RESTORE_TEMPLATES:
        raise ComfyError(f"unknown restore template {template!r}")
    return Plan(template, inputs, {"image": src}, [], "image", sources)


def image_tool_plan(t: dict, seed: int, lookup) -> Plan:
    """Edit Image tools (app.image_tools): line art / depth / edges, LaMa, IC-Light, CatVTON."""
    from app.image_tools import TEMPLATES

    template = t.get("template")
    if template not in TEMPLATES:
        raise ComfyError(f"unknown image tool template {template!r}")
    images: dict[str, Path | list[Path]] = {"image": lookup.generation_file(t["source_id"])}
    if t.get("mask_file"):
        mask = get_settings().data_dir / t["mask_file"]
        if not mask.is_file():
            raise ComfyError("the mask for this edit is missing on disk")
        images["mask"] = mask
    for key in ("garment", "background"):
        if t.get(f"{key}_id"):
            images[key] = lookup.generation_file(t[f"{key}_id"])
    inputs = {**(t.get("inputs") or {}), "seed": seed}
    sources = {k: t[k] for k in ("source_id", "garment_id", "background_id") if t.get(k)}
    return Plan(template, inputs, images, [], "image", {**sources, "tool": t.get("tool")})


def model3d_plan(rec: dict, seed: int, lookup) -> Plan:
    """3D Studio (contract v40): one picture through Pixal3D or TRELLIS.2, or up to four views through
    Pixal3D multi-view, to a GLB."""
    from app import model3d

    inputs = model3d.inputs_for(rec, seed)
    views = {v: rec["views"][v] for v in model3d.VIEWS if (rec.get("views") or {}).get(v)}
    if views:
        if "front" not in views:
            raise ComfyError("A four-view model needs a front view")
        sources = {"views": views, "engine": "pixal3d", "quality": rec.get("quality") or "final"}
        return Plan(model3d.MULTIVIEW_TEMPLATE, inputs, {v: lookup.generation_file(g) for v, g in views.items()},
                    [], "model", sources)
    if not rec.get("source_id"):
        raise ComfyError("A 3D model needs a source picture")
    sources = {"source_id": rec["source_id"], "engine": "trellis2" if inputs["trellis2"] else "pixal3d",
               "quality": rec.get("quality") or "final"}
    return Plan(model3d.TEMPLATE, inputs, {"image": lookup.generation_file(rec["source_id"])}, [], "model", sources)


def control_plan(c: dict, prompt: str, negative: str, seed: int, lookup) -> Plan:
    """Depth / Sketch / Pose / Edge to image (app.control): Z-Image Turbo with the Fun ControlNet patch."""
    from app.control import TEMPLATES

    template = c.get("template")
    if template not in TEMPLATES:
        raise ComfyError(f"unknown control template {template!r}")
    if c.get("control_file"):
        # an inverted copy of a dark-on-light sketch
        image = get_settings().data_dir / c["control_file"]
        if not image.is_file():
            raise ComfyError("the prepared control picture is missing on disk")
    else:
        image = lookup.generation_file(c["source_id"])
    inputs = {**(c.get("inputs") or {}), "prompt": prompt, "negative": negative, "seed": seed}
    return Plan(template, inputs, {"image": image}, [], "image",
                {"source_id": c["source_id"], "control": c.get("type"), "as_is": bool(c.get("as_is"))})


def song_language(params: dict) -> str:
    """The language the lyrics are sung in. The letters win over the picker: Urdu lyrics sent with the
    default "en" would otherwise be read as English and come out as some third language."""
    asked = str(params.get("language") or "en").lower()
    found = native_audio.language_of(params.get("lyrics") or "")
    if not found or found == "en" or found == asked.split("-")[0]:
        return asked
    # telling Urdu from Shahmukhi or Sindhi by the letters is a guess; the writer's pick stands
    if found in aerab.ARABIC_SCRIPT_LANGS and asked in (*aerab.ARABIC_SCRIPT_LANGS, "pa"):
        return asked
    return found


def style_tags(prompt: str, params: dict) -> str:
    """Prompt plus the vocal, language and tempo chips, the way ACE-Step and MiniMax read their style text."""
    parts = [prompt.strip().rstrip(",. ")]
    # no lyrics means instrumental whatever the vocal chip says: a voice with nothing to sing only hums
    sung = params.get("vocal") != "none" and (params.get("lyrics") or "").strip()
    if not sung:
        vocal = "instrumental"
    else:
        vocal = VOCALS.get(params.get("vocal") or "")
    if vocal and vocal not in parts[0].lower():
        parts.append(vocal)
    if sung:
        # ComfyUI's ACE-Step encoder never shows the language to its audio-code LM, only the caption and
        # lyrics, so the caption has to say it
        name = (native_audio.NAMES.get(song_language(params).split("-")[0]) or "").split(" (")[0]
        if name and name.lower() not in parts[0].lower():
            parts.append(f"sung in {name}")
    if params.get("bpm"):
        parts.append(f"{int(params['bpm'])} bpm")
    return ", ".join(p for p in parts if p)


def audio_plan(kind: str, prompt: str, params: dict, seed: int, lookup) -> Plan:
    """Audio Studio (contract v14): song / music / sfx on the model the API picked."""
    m = mc.MODELS.get(params.get("model") or "")
    if m is None or m.type != "audio":
        m = mc.audio_default(kind)
    duration = float(params.get("duration_s") or 30)
    inputs: dict[str, Any] = {"seed": seed, "duration_s": duration}
    if params.get("steps"):
        inputs["steps"] = int(params["steps"])
    images: dict[str, Path | list[Path]] = {}
    sources: dict[str, Any] = {"model": m.id}
    lyrics = (params.get("lyrics") or "").strip()

    if m.template == "ace15_song":
        lang = song_language(params)
        lang = ACE_NEAREST.get(lang, lang)
        if lyrics and not get_settings().song_lyrics_aerab:
            lyrics = aerab.strip(lyrics)
        inputs.update(tags=style_tags(prompt, params), lyrics=lyrics or "[Instrumental]",
                      language=lang if lang in ACE_LANGUAGES else "unknown")
        if params.get("bpm"):
            inputs["bpm"] = int(params["bpm"])
        if params.get("keyscale"):
            inputs["keyscale"] = params["keyscale"]
        if params.get("time_signature"):
            inputs["timesignature"] = str(params["time_signature"])
        if params.get("guidance") is not None:
            inputs["guidance"] = float(params["guidance"])
        if params.get("think") is not None:
            inputs["audio_codes"] = bool(params["think"])
        ref = params.get("timbre_ref_id")
        if ref:
            images["timbre_audio"] = lookup.generation_file(ref)
            # Comfy's own advice: the audio-code LM fights a reference clip, so it goes off
            inputs["audio_codes"] = False
            sources["timbre_ref_id"] = ref
    elif m.template == "minimax_music3":
        inputs.update(caption=style_tags(prompt, params), lyrics=lyrics)
    elif m.template == "yue2_song":
        # YuE2's template writes its markers as [Verse] / [Chorus]
        inputs.update(style=style_tags(prompt, params),
                      lyrics=re.sub(r"\[([a-z][a-z-]*)\]", lambda mt: f"[{mt[1].title()}]", lyrics))
    else:
        text = prompt.strip()
        hint = SA3_HINTS.get(params.get("category") or "")
        if hint and hint not in text.lower():
            text = f"{text.rstrip('. ')}, {hint}"
        if params.get("bpm"):
            text += f", {int(params['bpm'])} BPM"
        inputs["prompt"] = text
        if params.get("negative"):
            inputs["negative"] = params["negative"]
    return Plan(m.template, inputs, images, [], "audio", sources)


def exec_seconds(entry: dict) -> float | None:
    """GPU time from the history timestamps, so time spent queued behind other jobs isn't billed."""
    start = end = None
    for msg in (entry.get("status") or {}).get("messages") or []:
        if not (isinstance(msg, list) and len(msg) == 2 and isinstance(msg[1], dict)):
            continue
        ts = msg[1].get("timestamp")
        if msg[0] == "execution_start":
            start = ts
        elif msg[0] in ("execution_success", "execution_error", "execution_interrupted"):
            end = ts
    if start and end and end >= start:
        return round((end - start) / 1000.0, 3)
    return None


class ComfyDriver:
    name = "comfy"

    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: Callable[[], Awaitable[ComfyClient]] | None = None,
        lookup=None,
        lane: str | None = None,
    ):
        self.settings = settings or get_settings()
        s = self.settings
        self.lane = lane
        # image jobs on the image GPU, video jobs on the video GPU (COMFY_IMAGE_URLS / COMFY_VIDEO_URLS)
        self.urls = s.comfy_urls_for(lane)
        self._client_factory = client_factory or (
            lambda: pick_client(self.urls, s.comfy_auth_token, s.comfy_verify_tls)
        )
        self.lookup = lookup or DbLookup()

    async def _connect(self, progress_cb: ProgressCallback) -> ComfyClient:
        deadline = time.monotonic() + max(0.0, self.settings.comfy_start_wait_s)
        while True:
            try:
                return await self._client_factory()
            except ComfyError:
                if time.monotonic() >= deadline:
                    raise
            # progress keeps the job's heartbeat fresh while ComfyUI starts
            progress_cb(0.0, "Waiting for ComfyUI to start")
            await asyncio.sleep(5)

    def generate_image(self, prompt, params, seed, out_path: Path, progress_cb: ProgressCallback) -> DriverResult:
        return self._generate(prompt, params, seed, out_path, progress_cb)

    def generate_video(self, prompt, params, seed, out_path: Path, progress_cb: ProgressCallback) -> DriverResult:
        return self._generate(prompt, params, seed, out_path, progress_cb)

    def generate_audio(self, prompt, params, seed, out_path: Path, progress_cb: ProgressCallback) -> DriverResult:
        if params.get("kind") == "speech":
            # the speech engines aren't ComfyUI nodes; they run in their own venvs on this worker's GPU
            from app import speech

            return speech.synthesise(params, seed, out_path, progress_cb, lane=self.lane)
        return self._generate(prompt, params, seed, out_path, progress_cb)

    def transcribe(self, path: Path, language: str | None, progress_cb: ProgressCallback) -> dict:
        from app import speech

        return speech.transcribe(path, language, progress_cb, lane=self.lane)

    # Dub a video (contract v18): engine venvs on this worker's GPU, like speech
    def separate(self, src: Path, out_dir: Path, model: str, progress_cb: ProgressCallback) -> dict:
        from app.dub import engines

        return engines.separate(src, out_dir, model, progress_cb, lane=self.lane)

    def separate_stems(self, src: Path, out_dir: Path, model: str, progress_cb: ProgressCallback) -> dict:
        from app.dub import engines

        return engines.separate_stems(src, out_dir, model, progress_cb, lane=self.lane)

    def speaker_embed(self, src: Path, spans, progress_cb: ProgressCallback) -> list[list[float]]:
        from app.dub import engines

        return engines.speaker_embed(src, spans, progress_cb, lane=self.lane)

    def speak_many(self, params: dict, texts: list[str], outs: list[Path], seed, progress_cb: ProgressCallback):
        from app.dub import engines

        return engines.speak_many(params, texts, outs, seed, progress_cb, lane=self.lane)

    def transcribe_many(self, paths: list[Path], language, progress_cb: ProgressCallback, expect=None) -> list[str]:
        from app.dub import engines

        return engines.transcribe_many(paths, language, progress_cb, lane=self.lane)

    def generate_model(self, prompt, params, seed, out_path: Path, progress_cb: ProgressCallback) -> DriverResult:
        return self._generate(prompt, params, seed, out_path, progress_cb)

    def generate_text(self, prompt, params, seed, out_path: Path, progress_cb: ProgressCallback) -> DriverResult:
        raise ComfyError("Text generations go to the LLM lane, not ComfyUI")

    def _generate(self, prompt, params, seed, out_path, progress_cb) -> DriverResult:
        seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
        plan = plan_generation(params.get("kind", ""), prompt, params, seed, self.lookup)
        # the worker is sync; each job gets its own short event loop
        return asyncio.run(self._execute(plan, out_path, progress_cb, params))

    async def _execute(self, plan: Plan, out_path: Path, progress_cb: ProgressCallback, params: dict) -> DriverResult:
        progress_cb(0.0, "Connecting to ComfyUI")
        client = await self._connect(progress_cb)
        try:
            inputs = dict(plan.inputs)
            for key, value in plan.images.items():
                if isinstance(value, list):
                    inputs[key] = [await client.upload_image(p) for p in value]
                else:
                    inputs[key] = await client.upload_image(value)
            gen_id = params.get("generation_id") or out_path.stem
            inputs["filename_prefix"] = f"mixai/{gen_id}"

            graph, resolved = build(plan.template, inputs, plan.loras)
            if plan.batch:
                batch_latent(graph, *plan.batch)
                resolved.update(batch_size=plan.batch[2], batch_index=plan.batch[1])
            tpl = load(plan.template)
            weights = {tpl.node_id(t): w for t, w in tpl.manifest.get("progress_weights", {}).items()
                       if any(n.get("_meta", {}).get("title") == t for n in graph.values())}

            timeout = TIMEOUTS.get(plan.template, 1800.0)
            t0 = time.monotonic()
            try:
                prompt_id, entry = await client.run(graph, progress_cb, timeout=timeout, weights=weights)
            except ComfyError as e:
                if not gpu_memory.is_oom(str(e)):
                    raise
                # usually Ollama still holding a big model on this card: let it go and try the same graph once more
                log.warning("%s ran out of GPU memory (%s); freeing VRAM and retrying once", plan.template, e)
                progress_cb(0.0, "Freeing GPU memory and retrying")
                await asyncio.to_thread(gpu_memory.unload_ollama)
                await client.free_memory()
                t0 = time.monotonic()
                prompt_id, entry = await client.run(graph, progress_cb, timeout=timeout, weights=weights)
            wall = round(time.monotonic() - t0, 3)

            outs = [o for o in parse_outputs(entry) if o.kind == plan.media]
            if not outs:
                raise ComfyError(f"{plan.template} finished but produced no {plan.media} output")
            progress_cb(0.99, "Downloading result")
            if plan.batch and plan.batch[2] > 1:
                await self._download_batch(client, outs, graph, tpl.manifest, out_path, params, plan.batch[2])
            else:
                await client.download(self._pick(outs, graph, tpl.manifest), out_path)
            server = client.base_url
        finally:
            await client.close()

        gpu_seconds = exec_seconds(entry) or wall
        record = {
            "template": plan.template,
            "seed": resolved.get("seed"),
            "inputs": {k: v for k, v in resolved.items() if k != "filename_prefix"},
            "sources": plan.sources,
            "prompt_id": prompt_id,
            "server": server,
            "gpu_seconds": gpu_seconds,
            "wall_seconds": wall,
        }
        if "view" in plan.inputs:
            record["view"] = plan.inputs["view"]
        meta: dict[str, Any] = {"template": plan.template, "gpu_seconds": gpu_seconds}
        if plan.media == "audio":
            from app.reel import probe

            # MiniMax may stop early, so the file says how long it really is
            meta["duration_s"] = round(probe(out_path).duration, 3) or resolved.get("duration_s")
            meta["model"] = plan.sources.get("model")
        elif plan.media == "video" and "num_frames" in resolved:
            fps = float(resolved.get("fps") or 24)
            meta.update(duration_s=round(resolved["num_frames"] / fps, 3), width=resolved["width"], height=resolved["height"])
        elif plan.media == "video":
            meta.update(width=resolved.get("width"), height=resolved.get("height"))
        elif plan.media == "model":
            meta.update(engine=plan.sources.get("engine"), quality=plan.sources.get("quality"))
        else:
            meta.update(width=resolved.get("width"), height=resolved.get("height"))
        progress_cb(1.0, "Done")
        media_type = {"video": "video/mp4", "audio": "audio/flac", "model": "model/gltf-binary"}.get(plan.media, "image/png")
        return DriverResult(out_path, media_type, meta, params_update={"comfy": record})

    async def _download_batch(self, client, outs, graph, manifest, out_path: Path, params: dict, n: int) -> None:
        # SaveImage lists a batch in order, so file i is picture i
        ids = self._output_ids(graph, manifest)
        saved = [o for o in outs if o.node_id in ids] or outs
        if len(saved) < n:
            raise ComfyError(f"asked for {n} pictures in one batch, got {len(saved)}")
        for o, dest in zip(saved, [out_path, *(params.get("batch_out") or [])]):
            if dest:  # None: that picture was deleted while it waited
                await client.download(o, Path(dest))

    @staticmethod
    def _output_ids(graph: dict, manifest: dict) -> set[str]:
        wanted = {spec["node"] for spec in manifest.get("outputs", {}).values()}
        return {nid for nid, n in graph.items() if n.get("_meta", {}).get("title") in wanted}

    @staticmethod
    def _pick(outs: list[ComfyOutput], graph: dict, manifest: dict) -> ComfyOutput:
        ids = ComfyDriver._output_ids(graph, manifest)
        for o in outs:
            if o.node_id in ids:
                return o
        return outs[0]
