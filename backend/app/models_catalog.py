"""Model catalog (contract v6): every model the app can drive, with the template behind it.

Single source of truth for the pickers (GET /api/models), for request validation and for the
comfy driver's routing. Availability comes from check_template against a cached /object_info.
"""
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException

from app.workflows import check_template, frames_for


@dataclass(frozen=True)
class Speed:
    id: str
    label: str
    steps: int
    note: str
    cfg: float = 1.0
    shift: float | None = None
    lora: str | None = None
    # a speed that runs other weights on its own graph (Krea 2 Raw, HiDream-O1 Full, Qwen-Image 2.1 base)
    template: str | None = None
    sampler: str | None = None
    scheduler: str | None = None
    # other graph inputs a preset sets (Ideogram 4's scheduler mu / std)
    extra: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class Model:
    id: str
    type: str  # image | edit | video | upscale | audio | model3d
    label: str
    badge: str | None
    description: str
    capabilities: tuple[str, ...]
    template: str
    # other templates the model needs for some capability (cap -> template)
    extra: dict = field(default_factory=dict)
    speeds: tuple[Speed, ...] = ()
    default_speed: str | None = None
    max_refs: int | None = None
    # pictures ComfyUI may draw in one run (app.image_batch); the big models stay at 1 so 8 never meets 20 GB
    max_batch: int = 1
    max_duration_s: float | None = None
    est_seconds: float | None = None
    default: bool = False
    smooth_motion: bool = False  # the template carries the optional temporal x2 nodes
    licence: str | None = None  # the weights licence, shown beside the model in the picker
    # hidden and refused unless AUDIO_ALLOW_NONCOMMERCIAL (audio) / IMAGE_ALLOW_NONCOMMERCIAL (image, edit):
    # its licence limits commercial use
    noncommercial: bool = False
    # "conditional" when commercial use is allowed only under terms (Krea 2's revenue cap); else derived
    terms: str | None = None
    group: str | None = None  # picker section for image models, see IMAGE_GROUPS
    best_for: tuple[str, ...] = ()
    # a manifest "features" block this model needs on top of the shared graph (the 3D engines share one)
    feature: str | None = None
    vram_gb: float | None = None  # peak on the GPU box, for the admin's planning; not shown in the studio

    @property
    def licence_kind(self) -> str:
        return self.terms or ("noncommercial" if self.noncommercial else "commercial")


QWEN_LIGHTNING = "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors"
QWEN_TURBO = "Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors"
SINGLE_PASS_S = 256 / 24  # 257 frames, one LTX pass
WAN_FPS = 16.0
WAN_FRAMES = 81
SA3_LICENCE = "Stability AI Community Licence: free for commercial use under US$1M annual revenue"
APACHE = "Apache-2.0: free for commercial use"
# contract v32: weights licences, read from each model card / licence file on 2026-10-14
KREA_LICENCE = ("Krea 2 Community Licence: commercial use only under US$1M annual revenue, "
                "with content filtering in place")
FLUX_NC = "FLUX Non-Commercial Licence: non-commercial use only"
QWEN_RESEARCH = "Qwen Research Licence: research and evaluation only, no commercial use"
ANIMA_LICENCE = "CircleStone Labs Non-Commercial Licence (weights); the images it makes may be used commercially"
OPENRAIL_M = "CreativeML OpenRAIL-M: commercial use allowed, with use restrictions"
OPENRAIL_PP = "CreativeML OpenRAIL++-M: commercial use allowed, with use restrictions"
SDXL_LIGHTNING = "sdxl_lightning_8step_lora.safetensors"
ANIMA_TURBO = "anima-turbo-lora-v0.2.safetensors"
IDEOGRAM_NC = "Ideogram Non-Commercial Model Agreement (weights); code Apache-2.0"
# picker sections for image models, in order
IMAGE_GROUPS = ("fast", "photo", "text", "anime", "experimental")

MODELS: dict[str, Model] = {m.id: m for m in (
    Model("zimage_turbo", "image", "Z-Image Turbo", "FAST",
          "Photoreal pictures in a few seconds.", ("t2i", "i2i"), "zimage_t2i", extra={"i2i": "zimage_i2i"},
          est_seconds=7, default=True, licence=APACHE, group="fast", best_for=("photos", "everyday pictures"),
          max_batch=8),
    Model("qwen_image_2512", "image", "Qwen-Image 2512", "TEXT",
          "Best for writing inside the picture: signs, posters, titles, including Urdu.",
          ("t2i", "i2i", "text_render"), "qwen_image_t2i", extra={"i2i": "qwen_image_i2i"},
          speeds=(
              # full: the server template says 50 steps; 30 keeps most of the quality at 60% of the time
              Speed("full", "Full quality", 30, "Slowest, most detail", cfg=4.0, shift=3.1),
              Speed("lightning", "Lightning (4 steps)", 4, "Fast, nearly full quality", shift=3.1, lora=QWEN_LIGHTNING),
              Speed("turbo", "Turbo (2 steps)", 2, "Fastest draft", shift=3.0, lora=QWEN_TURBO),
          ),
          default_speed="lightning", est_seconds=25, licence=APACHE, group="text",
          best_for=("posters", "signs", "Urdu and other scripts")),
    Model("flux2_klein", "image", "FLUX.2 klein 4B", "FAST",
          "Very fast general-purpose images.", ("t2i", "i2i"), "flux2_klein_t2i", extra={"i2i": "flux2_klein_i2i"},
          est_seconds=5, licence=APACHE, group="fast", best_for=("batches", "quick drafts"), max_batch=8),

    # contract v32 (est_seconds are guesses for the RTX 4000 Ada until the server has run some)
    Model("realvis_xl5", "image", "RealVisXL V5.0", "PHOTO",
          "SDXL photoreal: people, skin and natural light.", ("t2i", "i2i"), "realvis_xl_t2i",
          extra={"i2i": "realvis_xl_i2i"},
          speeds=(
              Speed("full", "Full quality", 30, "DPM++ SDE Karras, 30 steps", cfg=5.0, sampler="dpmpp_sde",
                    scheduler="karras"),
              Speed("lightning", "Lightning (8 steps)", 8, "SDXL-Lightning LoRA, faster", lora=SDXL_LIGHTNING,
                    sampler="euler", scheduler="sgm_uniform"),
          ),
          default_speed="full", est_seconds=15, licence=OPENRAIL_PP, group="photo",
          best_for=("portraits", "realistic people")),
    Model("zimage_base", "image", "Z-Image (base)", "PHOTO",
          "Undistilled Z-Image: real CFG and negative prompts, slower and more controllable.", ("t2i", "i2i"),
          "zimage_base_t2i", extra={"i2i": "zimage_base_i2i"}, est_seconds=40, licence=APACHE, group="photo",
          best_for=("negative prompts", "fine control")),
    Model("hidream_o1", "image", "HiDream-O1-Image", "2K",
          "Detailed, well-composed pictures that follow long prompts.", ("t2i",), "hidream_o1_dev_t2i",
          speeds=(
              Speed("dev", "Dev (28 steps)", 28, "Distilled, cfg 1"),
              Speed("full", "Full (40 steps)", 40, "Base model with CFG, slowest", cfg=5.0, template="hidream_o1_t2i"),
          ),
          default_speed="dev", est_seconds=60, licence="MIT: free for commercial use", group="photo",
          best_for=("long prompts", "composition")),
    Model("ernie_image_turbo", "image", "ERNIE-Image Turbo", "TEXT",
          "8-step pictures with accurate text, infographics and layouts.", ("t2i", "text_render"),
          "ernie_turbo_t2i", est_seconds=20, licence=APACHE, group="text",
          best_for=("infographics", "comics", "layouts"), max_batch=8),
    Model("illustrious_xl2", "image", "Illustrious XL v2.0", "ANIME",
          "SDXL anime and illustration: characters, key visuals, manga looks.", ("t2i", "i2i"),
          "illustrious_xl_t2i", extra={"i2i": "illustrious_xl_i2i"}, est_seconds=12, licence=OPENRAIL_M,
          group="anime", best_for=("anime", "manga", "characters")),
    Model("anima", "image", "Anima", "ANIME",
          "2B anime model: clean line art and many styles.",
          ("t2i", "i2i"), "anima_t2i", extra={"i2i": "anima_i2i"},
          speeds=(
              Speed("turbo", "Turbo (8 steps)", 8, "Official turbo LoRA, cfg 1", lora=ANIMA_TURBO),
              Speed("full", "Full quality", 30, "30 steps, cfg 4", cfg=4.0),
          ),
          default_speed="turbo", est_seconds=5, licence=ANIMA_LICENCE, noncommercial=True, group="anime",
          best_for=("anime", "illustration")),
    Model("krea2_turbo", "image", "Krea 2", "2K",
          "Aesthetic, stylised pictures from 1K to 2K.", ("t2i",), "krea2_t2i",
          speeds=(
              Speed("turbo", "Turbo (8 steps)", 8, "Distilled, cfg 1"),
              Speed("raw", "Raw (52 steps)", 52, "Undistilled base with CFG, much slower", cfg=3.5,
                    template="krea2_raw_t2i"),
          ),
          default_speed="turbo", est_seconds=25, licence=KREA_LICENCE, noncommercial=True, terms="conditional",
          group="experimental", best_for=("art direction", "stylised looks"), max_batch=8),
    Model("flux2_klein_9b", "image", "FLUX.2 klein 9B", "FAST",
          "Bigger klein: more detail than 4B at the same 4 steps.", ("t2i", "i2i"), "flux2_klein9b_t2i",
          extra={"i2i": "flux2_klein9b_i2i"}, est_seconds=10, licence=FLUX_NC, noncommercial=True,
          group="experimental", best_for=("quick drafts", "detail"), max_batch=8),
    Model("qwen_image_21", "image", "Qwen-Image 2.1", "TEXT",
          "Newer Qwen-Image: sharper text and 2K detail. 2512 stays the default.",
          ("t2i", "text_render"), "qwen_image21_t2i",
          speeds=(
              Speed("turbo", "Turbo (8 steps)", 8, "Turbo weights, cfg 1"),
              Speed("full", "Full (25 steps)", 25, "Base weights, slowest", template="qwen_image21_full_t2i"),
          ),
          default_speed="turbo", est_seconds=35, licence=QWEN_RESEARCH, noncommercial=True, group="experimental",
          best_for=("posters", "small text")),
    Model("ideogram4", "image", "Ideogram 4", "TEXT",
          "Exact text, posters and clean layouts; follows long, structured prompts.", ("t2i", "text_render"),
          "ideogram4_t2i",
          speeds=(
              # official presets (ideogram-oss inference.md): cfg 3 only for the last 1 / 2 / 3 steps
              Speed("turbo", "Turbo (12 steps)", 12, "Fastest",
                    extra=(("mu", 0.5), ("std", 1.75), ("polish_from", 0.917))),
              Speed("default", "Default (20 steps)", 20, "Balanced",
                    extra=(("mu", 0.0), ("std", 1.75), ("polish_from", 0.9))),
              Speed("quality", "Quality (48 steps)", 48, "Most detail, slowest",
                    extra=(("mu", 0.0), ("std", 1.5), ("polish_from", 0.9375))),
          ),
          # two 8.6 GB UNets plus a 9.9 GB encoder: they take turns on the 20 GB card, so first runs are slow
          default_speed="default", est_seconds=90, licence=IDEOGRAM_NC, noncommercial=True, group="experimental",
          best_for=("posters", "signs", "typography")),
    Model("chroma1_hd", "image", "Chroma1-HD", "NEW",
          "Open FLUX-based model with real CFG and negatives; wide range of styles.", ("t2i",), "chroma1_hd_t2i",
          est_seconds=60, licence=APACHE, group="experimental", best_for=("styles", "negative prompts")),
    Model("wan22_still", "image", "Wan 2.2 Cinematic still", "NEW",
          "A single film frame from the Wan 2.2 video model: cinematic light and framing.", ("t2i",),
          "wan22_still_t2i", est_seconds=60, licence=APACHE, group="experimental",
          best_for=("film stills", "keyframes")),

    Model("qwen_image_edit_2511", "edit", "Qwen-Image-Edit 2511", "BEST",
          "Change a picture or combine up to 3 references; keeps faces and clothes.",
          ("edit", "refs", "multi_angle"), "qwen_edit", max_refs=3, est_seconds=20, default=True, licence=APACHE),
    Model("flux2_klein_edit", "edit", "FLUX.2 klein 4B (base)", None,
          "Reference-guided edits, quick and loose.", ("edit", "refs"), "flux2_klein_edit",
          max_refs=3, est_seconds=25, licence=APACHE),
    Model("flux2_klein_9b_edit", "edit", "FLUX.2 klein 9B", None,
          "Reference-guided edits in 4 steps with the 9B model.", ("edit", "refs"), "flux2_klein9b_edit",
          max_refs=3, est_seconds=15, licence=FLUX_NC, noncommercial=True),

    Model("ltx23_distilled", "video", "LTX-2.3", None,
          "Video with sound from text, a start image, or start and end frames; long takes.",
          ("t2v", "i2v", "flf", "audio", "longtake"), "ltx23_i2v", extra={"longtake": "ltx23_extend"},
          max_duration_s=None, est_seconds=40, default=True, smooth_motion=True),
    Model("ltx23_hq", "video", "LTX-2.3 High quality", "HQ",
          "Sharper, higher-resolution video with sound; slower, one shot up to about 10 s.",
          ("t2v", "i2v", "flf", "audio"), "ltx23_two_stage", max_duration_s=SINGLE_PASS_S,
          est_seconds=150, smooth_motion=True),
    Model("wan22_t2v", "video", "Wan 2.2 14B", "NEW",
          "Text to video, no sound, about 5 seconds. Strong motion.", ("t2v",), "wan22_t2v",
          max_duration_s=5.0, est_seconds=120),

    Model("seedvr2", "upscale", "SeedVR2", "BEST", "Best video upscale: restores detail.", (), "upscale_seedvr2",
          default=True),
    Model("flashvsr", "upscale", "FlashVSR", "FAST", "Fast video upscale.", (), "upscale_flashvsr"),
    Model("esrgan", "upscale", "ESRGAN", "QUICK", "Quickest upscale, sharpening only.", (), "upscale_esrgan"),
    Model("zimage_redraw", "upscale", "Z-Image redraw", "UPSCALE",
          "Image upscale that redraws fine detail.", (), "image_upscale_zimage"),

    # audio est_seconds: per 30 s of output, warm (the UI scales by length; guesses until measured)
    Model("ace15_turbo", "audio", "ACE-Step 1.5", "SONG",
          "Full songs with vocals from style tags and lyrics, 50+ languages including Urdu and Hindi.",
          ("song", "lyrics", "vocals", "timbre_ref"), "ace15_song", max_duration_s=300, est_seconds=5,
          default=True, licence="MIT licence: free for commercial use"),
    Model("minimax_music3", "audio", "MiniMax-Music3", None,
          "Songs with vocals from a description and lyrics; may end a little before the length you ask for.",
          ("song", "lyrics", "vocals"), "minimax_music3", max_duration_s=240, est_seconds=18,
          licence="MiniMax-Music3 Community Licence: commercial use only under US$20M annual revenue",
          noncommercial=True),
    Model("yue2", "audio", "YuE2", "SCORED",
          "Songs with vocals that follow a written melody and chords; strong on long, structured songs. "
          "Settles its own length, up to the one you ask for.",
          ("song", "lyrics", "vocals"), "yue2_song", max_duration_s=360, est_seconds=12,
          # TODO: owner is testing YuE2 first; settle its licence before launch
          licence="Test use: licence to be confirmed before release"),
    Model("sa3_small_music", "audio", "Stable Audio 3 Small Music", "FAST",
          "Instrumental music, beds and loops up to 2 minutes.", ("music",), "sa3_music", max_duration_s=120,
          est_seconds=3, default=True, licence=SA3_LICENCE),
    Model("sa3_medium", "audio", "Stable Audio 3 Medium", "LONG",
          "Longer, more structured instrumental pieces, up to about 6 minutes.", ("music", "long"), "sa3_medium",
          max_duration_s=380, est_seconds=9, licence=SA3_LICENCE),
    Model("sa3_small_sfx", "audio", "Stable Audio 3 Small SFX", "SFX",
          "Sound effects, one-shots and ambience up to 2 minutes.", ("sfx",), "sa3_sfx", max_duration_s=120,
          est_seconds=3, default=True, licence=SA3_LICENCE),

    # contract v40: image to 3D. est_seconds is a Final run on the RTX 4000 Ada, guessed until the server times one
    Model("pixal3d", "model3d", "Pixal3D", None, "True to the picture: keeps the shape and proportions you show it.",
          ("image_to_3d", "multiview"), "trellis2_image_to_3d", {"multiview": "pixal3d_multiview_to_3d"},
          feature="pixal3d", est_seconds=300, default=True,
          licence="MIT: free for commercial use", vram_gb=18, best_for=("products", "props", "your own photos")),
    Model("trellis2", "model3d", "TRELLIS.2", None, "General image to 3D: clean shapes from any picture.",
          ("image_to_3d",), "trellis2_image_to_3d", feature="trellis2", est_seconds=270,
          licence="MIT: free for commercial use", vram_gb=16, best_for=("characters", "concept art")),
)}

TYPES = ("image", "edit", "video", "upscale", "audio", "model3d")
AUDIO_TEMPLATES = frozenset(m.template for m in MODELS.values() if m.type == "audio")
# what a generation kind needs from an audio model, and how to say it in an error
AUDIO_KIND_LABELS = {"song": "songs", "music": "music", "sfx": "sound effects"}
QUALITIES = {"standard": "ltx23_distilled", "hq": "ltx23_hq"}


def default_for(type_: str) -> Model:
    return next(m for m in MODELS.values() if m.type == type_ and m.default)


def audio_default(kind: str) -> Model:
    return next(m for m in MODELS.values() if m.type == "audio" and m.default and kind in m.capabilities)


def nc_switch(m: Model) -> str | None:
    """The .env switch that allows this model's non-commercial weights, None for other types."""
    if m.type == "audio":
        return "AUDIO_ALLOW_NONCOMMERCIAL"
    if m.type in ("image", "edit"):
        return "IMAGE_ALLOW_NONCOMMERCIAL"
    return None


def hidden(m: Model) -> bool:
    from app.config import get_settings

    switch = nc_switch(m)
    if not m.noncommercial or switch is None:
        return False
    return not getattr(get_settings(), switch.lower())


def check_audio(m: Model, kind: str, duration_s: float) -> None:
    if kind not in m.capabilities:
        makes = " and ".join(AUDIO_KIND_LABELS[c] for c in m.capabilities if c in AUDIO_KIND_LABELS)
        raise HTTPException(422, f"{m.label} makes {makes}, not {AUDIO_KIND_LABELS.get(kind, kind)}")
    if m.max_duration_s is not None and duration_s > m.max_duration_s + 1e-6:
        raise HTTPException(422, f"{m.label} makes up to {m.max_duration_s:g} s; asked for {duration_s:g} s")


def get(model_id: str | None, type_: str) -> Model:
    """The model, or the type's default when none was asked for. 422 on unknown / wrong type /
    known-unavailable. "auto" with nothing to go on is the default too (see resolve_auto)."""
    if not model_id or model_id == AUTO:
        return default_for(type_)
    m = MODELS.get(model_id)
    if m is not None and hidden(m):
        raise HTTPException(422, f"{m.label} isn't available on this server.")
    if m is None or m.type != type_:
        names = ", ".join(x.id for x in MODELS.values() if x.type == type_ and not hidden(x))
        what = "an image" if type_ == "image" else ("an edit" if type_ == "edit" else f"a {type_}")
        raise HTTPException(422, f"'{model_id}' isn't {what} model (choose one of: {names})")
    reason = known_unavailable(m)
    if reason:
        raise HTTPException(422, f"{m.label} isn't available right now: {reason}")
    return m


# ---------------------------------------------------------------- auto

AUTO = "auto"
AUTO_TYPES = ("image", "edit", "video", "audio")
AUTO_DESCRIPTION = "Picks the best model for your prompt"
_NON_LATIN = re.compile(r"[֐-ࣿऀ-෿฀-࿿ᄀ-ᇿ぀-ヿ㐀-鿿가-힯"
                        r"Ѐ-ӿͰ-Ͽﭐ-﷿ﹰ-﻿]")
_TEXT_WORDS = re.compile(r"\b(posters?|signs?|signage|signboard|logos?|text|caption|headline|lettering|typography|"
                         r"banner|billboard)\b", re.I)


_ANIME_WORDS = re.compile(r"\b(anime|manga|chibi|waifu|kawaii|cel[- ]shad(?:ed|ing)|visual novel|shoujo|shojo|"
                          r"shonen|shounen|isekai|vtuber)\b", re.I)
# contract v32: Auto may send anime prompts here, in this order, once /object_info shows them installed
AUTO_ANIME = ("illustrious_xl2", "anima")


def wants_text(prompt: str) -> bool:
    from app.prompt_enhance import quoted

    return bool(quoted(prompt) or _NON_LATIN.search(prompt or "") or _TEXT_WORDS.search(prompt or ""))


def wants_anime(prompt: str) -> bool:
    return bool(_ANIME_WORDS.search(prompt or ""))


def _usable(model_id: str) -> bool:
    m = MODELS[model_id]
    return not hidden(m) and known_unavailable(m) is None


def _confirmed(model_id: str) -> bool:
    """Stricter than _usable for the newer routes: the model must be allowed here and the last /object_info
    must show it installed. Not knowing keeps Auto on the old default, so nobody's outputs change by surprise."""
    from app.config import get_settings

    m = MODELS[model_id]
    if hidden(m):
        return False
    if get_settings().gen_driver == "mock":
        return True
    info = _cached_info()
    return info is not None and availability(m, info)["available"]


def resolve_auto(type_: str, prompt: str = "", *, count: int = 1, refs: int = 0,
                 quality: str | None = None, kind: str | None = None, cap: str | None = None,
                 allowed: Callable[[str], bool] | None = None) -> tuple[Model, str]:
    """(model, why) for the catalog's "auto" entry. Skips a pick the last /object_info says is broken, and
    with `cap` (img2img asks for "i2i") a pick that can't do it."""
    if type_ == "image":
        if wants_text(prompt):
            picks = [("qwen_image_2512", "text in the prompt")]
        elif wants_anime(prompt):
            picks = [(mid, "anime or manga prompt") for mid in AUTO_ANIME if _confirmed(mid)]
        else:
            picks = []
        if count >= 4 and not picks:
            picks = [("flux2_klein", f"{count} images, fastest model")]
        picks.append(("zimage_turbo", "general picture"))
        if cap:
            picks = [p for p in picks if capability_unavailable(MODELS[p[0]], cap) is None] or picks[-1:]
    elif type_ == "edit":
        picks = [(m.id, "keeps faces and clothes" if m.id == "qwen_image_edit_2511" else f"{refs} references")
                 for m in MODELS.values() if m.type == "edit" and not hidden(m)
                 and (m.max_refs is None or refs <= m.max_refs)]
        picks.sort(key=lambda p: p[0] != "qwen_image_edit_2511")
        picks.append(("qwen_image_edit_2511", "default"))
    elif type_ == "video":
        picks = [("ltx23_hq", "high quality asked for")] if quality == "hq" else []
        picks.append(("ltx23_distilled", "video with sound"))
    elif type_ == "audio":
        m = audio_default(kind or "song")
        picks = [(m.id, f"default for {AUDIO_KIND_LABELS.get(kind or 'song', kind)}")]
    else:
        raise HTTPException(422, f"There's no auto choice for {type_} models")
    if allowed is not None:
        # app.rights: only what this person's plan has; failing the usual picks, any of those that fits
        picks = [p for p in picks if allowed(p[0])] or [
            (m.id, "in your plan") for m in MODELS.values()
            if m.type == type_ and allowed(m.id) and not hidden(m)
            and (type_ != "audio" or (kind or "song") in m.capabilities)]
        if not picks:
            raise HTTPException(403, f"No {type_} model is part of your plan")
    for mid, why in picks:
        if _usable(mid):
            return MODELS[mid], why
    # nothing known-good: hand back the last resort and let get()/the job say why
    mid, why = picks[-1]
    return get(mid, type_), why


def choose(model_id: str | None, type_: str, prompt: str = "", *, allowed: Callable[[str], bool] | None = None,
           **kw) -> tuple[Model, dict]:
    """Route helper: the model plus what goes into params (model_resolved, and the reason when auto).
    `allowed` narrows Auto to what the person may use (app.rights.choose passes it)."""
    # audio has a default per kind, so leaving the model out means auto there
    if model_id == AUTO or (type_ == "audio" and not model_id):
        m, why = resolve_auto(type_, prompt, allowed=allowed, **kw)
        return m, {"model_requested": AUTO, "model_resolved": m.id, "model_auto_reason": why}
    m = get(model_id, type_)
    return m, {"model_resolved": m.id}


def auto_out(type_: str) -> dict:
    return {"id": AUTO, "type": type_, "label": "Auto", "badge": None, "description": AUTO_DESCRIPTION,
            "capabilities": [], "available": True, "reason": None, "default": False, "auto": True,
            "est_seconds": None, "estimate_source": "rough"}


def speed_of(m: Model, speed_id: str | None) -> Speed | None:
    if not m.speeds:
        if speed_id:
            raise HTTPException(422, f"{m.label} has no speed options")
        return None
    sid = speed_id or m.default_speed
    sp = next((s for s in m.speeds if s.id == sid), None)
    if sp is None:
        raise HTTPException(422, f"Unknown speed '{speed_id}' for {m.label} "
                                 f"(choose one of: {', '.join(s.id for s in m.speeds)})")
    return sp


def template_for(m: Model, speed_id: str | None = None, *, i2i: bool = False) -> str:
    """The graph the comfy driver runs for this model/speed (the mock driver records it too)."""
    if i2i and "i2i" in m.extra:
        return m.extra["i2i"]
    sp = next((s for s in m.speeds if s.id == (speed_id or m.default_speed)), None)
    return sp.template if sp is not None and sp.template else m.template


def check_refs(m: Model, n: int) -> None:
    if m.max_refs is not None and n > m.max_refs:
        raise HTTPException(422, f"{m.label} takes at most {m.max_refs} reference images; you sent {n}")


def check_video(m: Model, *, has_image: bool, duration_s: float, smooth_motion: bool) -> None:
    if has_image and "i2v" not in m.capabilities:
        raise HTTPException(422, f"{m.label} makes video from text only; remove the start image or pick LTX-2.3")
    if m.max_duration_s is not None and duration_s > m.max_duration_s + 1e-6:
        raise HTTPException(422, f"{m.label} makes clips up to {m.max_duration_s:.1f} s; asked for {duration_s:g} s")
    if smooth_motion:
        if not m.smooth_motion:
            raise HTTPException(422, f"Smooth motion isn't offered for {m.label}")
        reason = smooth_unavailable(m)
        if reason:
            raise HTTPException(422, f"Smooth motion isn't available right now: {reason}")


# ---------------------------------------------------------------- availability

def _problems(res: dict) -> list[str]:
    return ([f"missing nodes: {', '.join(res['missing_nodes'])}"] if res["missing_nodes"] else []) + \
           ([f"missing models: {', '.join(res['missing_models'])}"] if res["missing_models"] else []) + \
           res["invalid"][:3]


def _lora_options(info: dict) -> set:
    spec = info.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name") or [[]]
    head = spec[0]
    if head == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return set(spec[1].get("options") or [])
    return set(head) if isinstance(head, list) else set()


def availability(m: Model, info: dict | None, *, driver: str = "comfy", error: str | None = None) -> dict:
    """{available, installed, reason, speeds: {id: reason|None}, smooth: reason|None, dropped: [capabilities]}
    installed is None when ComfyUI couldn't be asked, False when nodes or weights are missing."""
    out = {"available": True, "installed": True, "reason": None, "speeds": {s.id: None for s in m.speeds},
           "smooth": None, "dropped": []}
    if driver == "mock":
        return out
    if info is None:
        reason = f"ComfyUI isn't reachable ({error or 'no answer'})"
        out.update(available=False, installed=None, reason=reason, smooth=reason)
        return out
    res = check_template(m.template, info)
    if not res["ok"]:
        out.update(available=False, reason="; ".join(_problems(res)),
                   installed=not (res["missing_nodes"] or res["missing_models"]))
    for cap, tpl in m.extra.items():
        extra = check_template(tpl, info)
        if not extra["ok"]:
            out["dropped"].append(cap)
    if m.speeds:
        loras = _lora_options(info)
        for s in m.speeds:
            if s.template and s.template != m.template:
                own = check_template(s.template, info)
                if not own["ok"]:
                    out["speeds"][s.id] = "not installed: " + "; ".join(_problems(own))
                    continue
            if s.lora and s.lora not in loras:
                out["speeds"][s.id] = f"missing LoRA {s.lora}"
    if m.smooth_motion:
        feat = (res.get("features") or {}).get("smooth_motion")
        if feat and not feat["ok"]:
            out["smooth"] = "; ".join(feat["problems"][:3])
    if m.feature:
        feat = (res.get("features") or {}).get(m.feature)
        if feat and not feat["ok"] and out["available"]:
            out.update(available=False, installed=False, reason="; ".join(feat["problems"][:3]))
    return out


def _cached_info() -> dict | None:
    from app import upscale  # its object_info cache is shared by every picker

    info = upscale._INFO_CACHE["info"]
    if info is None or time.monotonic() - upscale._INFO_CACHE["at"] > upscale.INFO_TTL_S:
        return None
    return info


def known_unavailable(m: Model) -> str | None:
    """Reason from a recent /object_info, None when fine or not known (we don't block on a guess)."""
    from app.config import get_settings

    if get_settings().gen_driver == "mock":
        return None
    info = _cached_info()
    return None if info is None else availability(m, info)["reason"]


def capability_unavailable(m: Model, cap: str) -> str | None:
    """Why `cap` can't be used right now: not offered at all, or its extra template failed the last
    /object_info check. None when fine or not known."""
    from app.config import get_settings

    if cap not in m.capabilities:
        return f"{m.label} doesn't offer this"
    if get_settings().gen_driver == "mock" or cap not in m.extra:
        return None
    info = _cached_info()
    if info is None:
        return None
    res = check_template(m.extra[cap], info)
    return None if res["ok"] else "; ".join(_problems(res))


def smooth_unavailable(m: Model) -> str | None:
    from app.config import get_settings

    if get_settings().gen_driver == "mock":
        return None
    info = _cached_info()
    return None if info is None else availability(m, info)["smooth"]


def measured_seconds(rows: list[dict]) -> dict[str, float]:
    """Median GPU seconds per output (images) or per output second (videos), keyed by catalog key.
    rows are generation params dicts with a 'comfy' record."""
    acc: dict[str, list[float]] = {}
    for p in rows:
        rec = (p or {}).get("comfy") or {}
        gpu = rec.get("gpu_seconds")
        tpl = rec.get("template")
        if not gpu or not tpl:
            continue
        key = tpl + (f":{p['speed']}" if p.get("speed") else "")
        if isinstance(p.get("model3d"), dict):
            # one graph, two engines: the card shows a Final run of its own engine
            if p["model3d"].get("quality") != "final":
                continue
            key = f"{tpl}:{p['model3d'].get('engine')}"
        per_second = tpl in ("ltx23_i2v", "ltx23_two_stage", "wan22_t2v") or tpl in AUDIO_TEMPLATES
        dur = p.get("duration_s") if per_second else None
        acc.setdefault(key, []).append(float(gpu) / float(dur) if dur else float(gpu))
    return {k: statistics.median(v[:15]) for k, v in acc.items()}


def model_out(m: Model, av: dict, measured: dict[str, float] | None = None) -> dict:
    measured = measured or {}
    caps = [c for c in m.capabilities if c not in av["dropped"]]
    est, source = m.est_seconds, "rough"
    key = m.template + (f":{m.default_speed}" if m.default_speed else "")
    if m.type == "model3d":
        key = f"{m.template}:{m.id}"
    if key in measured:
        per = measured[key]
        est = per * {"video": 5.0, "audio": 30.0}.get(m.type, 1.0)  # a typical 5 s clip / per 30 s of audio
        source = "measured"
    out = {
        "id": m.id, "type": m.type, "label": m.label, "badge": m.badge, "description": m.description,
        "capabilities": caps, "available": av["available"], "reason": av["reason"], "default": m.default,
        "est_seconds": round(est) if est is not None else None, "estimate_source": source,
    }
    if m.speeds:
        out["speeds"] = [{"id": s.id, "label": s.label, "steps": s.steps, "note": s.note,
                          "available": av["speeds"][s.id] is None, "reason": av["speeds"][s.id]} for s in m.speeds]
        out["default_speed"] = m.default_speed
    if m.max_refs is not None:
        out["max_refs"] = m.max_refs
    if m.type in ("image", "edit"):
        # contract v32: the weights licence on every card, the picker section, and whether it's installed
        out["extra"] = {"licence": m.licence, "licence_kind": m.licence_kind,
                        **({"noncommercial": True} if m.noncommercial else {})}
        out["installed"] = av.get("installed", True)
        if m.group:
            out["group"] = m.group
        if m.best_for:
            out["best_for"] = list(m.best_for)
    if m.type == "audio":
        out["max_duration_s"] = m.max_duration_s
        out["extra"] = {"licence": m.licence, **({"noncommercial": True} if m.noncommercial else {})}
    if m.type == "model3d":
        out["installed"] = av.get("installed", True)
        if m.best_for:
            out["best_for"] = list(m.best_for)
    if m.type == "video":
        if m.max_duration_s is not None:
            out["max_duration_s"] = round(m.max_duration_s, 2)
        else:
            from app.config import get_settings

            out["max_duration_s"] = get_settings().longtake_max_s
        out["audio"] = "audio" in caps
        if m.smooth_motion:
            out["smooth_motion"] = {"available": av["smooth"] is None and av["available"], "reason": av["smooth"]}
    return out


def catalog(type_: str | None, info: dict | None, *, driver: str, error: str | None = None,
            measured: dict | None = None) -> list[dict]:
    out = []
    for t in TYPES:
        if type_ not in (None, t):
            continue
        rows = [model_out(m, availability(m, info, driver=driver, error=error), measured)
                for m in MODELS.values() if m.type == t and not hidden(m)]
        if t in AUTO_TYPES:
            auto = auto_out(t)
            if not any(r["available"] for r in rows):
                auto.update(available=False, reason=rows[0]["reason"])
            out.append(auto)
        out += rows
    return out


# ---------------------------------------------------------------- studio params

IMAGE_STUDIO_KINDS = ("portrait", "establishing", "keyframe_start", "keyframe_end", "keyframe_mid")


def clean_project_settings(current: dict, patch: dict) -> dict:
    """Merge a settings patch, validating the keys this module owns; other keys (brand kits…) pass through."""
    out = dict(current)
    for key, val in patch.items():
        if key in ("brand_closing_shot", "shots_review"):
            continue  # owned by brand_moments / the storyboard review gate, not settable from outside
        if val is None:
            out.pop(key, None)
            continue
        if key in ("image_model", "edit_model") and val == AUTO:
            # TODO: resolve per prompt in the studio too; for now auto there means the type's default
            out.pop(key, None)
            continue
        if key == "image_model":
            val = get(str(val), "image").id
        elif key == "image_speed":
            speed_of(get(str(patch.get("image_model") or out.get("image_model") or ""), "image"), str(val))
        elif key == "edit_model":
            val = get(str(val), "edit").id
        elif key == "video_quality":
            if val not in QUALITIES:
                raise HTTPException(422, f"video_quality must be one of: {', '.join(QUALITIES)}")
        elif key == "smooth_motion":
            val = bool(val)
        elif key == "lipsync_engine":
            if val not in ("ltx", "wan_s2v"):
                raise HTTPException(422, "lipsync_engine must be one of: ltx, wan_s2v")
        elif key == "brand_closing":
            if val not in ("auto", "ai_packshot", "logo_reveal", "none"):
                raise HTTPException(422, "brand_closing must be one of: auto, ai_packshot, logo_reveal, none")
        out[key] = val
    return out


def apply_image_choice(params: dict, settings: dict | None) -> dict:
    """Studio stills: params.model (an image model) / params.edit_model, else the project's defaults.
    Validates; leaves the params without a model when everything is the default."""
    settings = settings or {}
    image_id = params.get("model") or settings.get("image_model")
    if image_id:
        params["model"] = get(image_id, "image").id
        sp = speed_of(MODELS[params["model"]], params.get("speed") or (
            settings.get("image_speed") if params["model"] == settings.get("image_model") else None))
        if sp:
            params["speed"] = sp.id
    edit_id = params.get("edit_model") or settings.get("edit_model")
    if edit_id:
        em = get(edit_id, "edit")
        params["edit_model"] = em.id
        check_refs(em, len(params.get("reference_ids") or []))
    return params


def apply_take_quality(params: dict, settings: dict | None, *, long_take: bool) -> dict:
    """Render quality for a take. HQ and smooth motion are single-pass only: asked for explicitly on a
    long take that's a 422, inherited from the project default it falls back with a note."""
    settings = settings or {}
    explicit_q = params.get("quality") is not None
    quality = params.get("quality") or settings.get("video_quality") or "standard"
    if quality not in QUALITIES:
        raise HTTPException(422, f"quality must be one of: {', '.join(QUALITIES)}")
    explicit_s = params.get("smooth_motion") is not None
    smooth = bool(params["smooth_motion"] if explicit_s else settings.get("smooth_motion"))
    notes = []
    if long_take and quality == "hq":
        if explicit_q:
            raise HTTPException(422, f"High quality renders one pass of up to {SINGLE_PASS_S:.1f} s; this take is "
                                     f"{float(params.get('duration_s') or 0):g} s. Use Standard, or shorten the shot.")
        quality = "standard"
        notes.append("long take rendered in Standard quality (High quality is single-pass only)")
    if long_take and smooth:
        if explicit_s:
            raise HTTPException(422, "Smooth motion works on single-pass takes only (up to "
                                     f"{SINGLE_PASS_S:.1f} s); turn it off for this long take")
        smooth = False
        notes.append("smooth motion skipped on a long take")
    m = get(QUALITIES[quality], "video")
    if smooth:
        check_video(m, has_image=True, duration_s=0, smooth_motion=True)
    params.update(quality=quality, smooth_motion=smooth)
    if notes:
        params["quality_note"] = "; ".join(notes)
    else:
        params.pop("quality_note", None)
    return params


def wan_frames(duration_s: float) -> int:
    # Wan latents step 4 frames; 81 (5 s at 16 fps) is what both experts were trained on
    n = int(round(duration_s * WAN_FPS / 4)) * 4 + 1
    return max(17, min(WAN_FRAMES, n))


def video_frames(m: Model, duration_s: float, fps: float = 24.0) -> int:
    return wan_frames(duration_s) if m.id == "wan22_t2v" else frames_for(duration_s, fps)
