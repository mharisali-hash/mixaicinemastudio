"""Photo Studio Restore and Cut-out (contract v10): ComfyUI does the model work, this module does the rest.

Every tool makes a new version of the picture, like Develop does. ComfyUI gets the picture at a size its
model can handle; finish() brings the result back to the source's full size on the CPU (strength blend,
alpha kept, prompted fixes pasted only where something changed, cut-outs composited from a stored mask).

Smart Restore follows the NoorViz plan (analysis -> ordered steps with reasons) but runs on the server:
one version per step, chained by a finished-job hook. A failed step doesn't end the chain; the next step
works from the last good version, as NoorViz did.
"""
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from PIL import Image, ImageFilter, ImageOps
from sqlalchemy.orm import Session

from app.config import get_settings
from app.limits import PROMPT_MAX
from app.models import Generation, Job, new_id
from app.photo import analysis, cutout as co
from app.photo import service as svc
from app.services import generation_file

log = logging.getLogger("mixai.photo.restore")

GENERATE_JOB = "generate"
FIX_MP = 1.05  # the edit models work at ~1 MP
SUPIR_MP = 1.5
MAX_INPUT_MP = 60.0


@dataclass(frozen=True)
class Tool:
    id: str
    label: str
    hint: str
    group: str  # repair | clean | faces | colour | style | cutout | finish
    template: str | None = None  # None: a CPU effect (effect) or the edit model (edit=True)
    effect: str | None = None
    edit: bool = False
    variants: dict = field(default_factory=dict)  # id -> (label, value fed to the template)
    default_variant: str | None = None
    cap_mp: float | None = None  # process at most this many megapixels, then resize back
    multiple: int = 2
    strength: bool = True  # offer the blend-with-original slider
    prompt: str | None = None  # "required" | "optional" | None
    noncommercial: bool = False
    licence: str | None = None  # shown beside tools whose weights carry terms worth knowing
    load_s: float = 5.0
    rate_mp: float = 1.0


TOOLS: dict[str, Tool] = {t.id: t for t in (
    Tool("repair", "Repair damage", "Faithful repair of fading, blur and wear. Keeps the photo true to itself.",
         "repair", "image_upscale_seedvr2", variants={"3b": ("Standard", "seedvr2_3b_int8_convrot.safetensors"),
                                                      "7b": ("Best · slower", "seedvr2_7b_int8_convrot.safetensors")},
         default_variant="3b", load_s=30, rate_mp=6.0),
    Tool("scratches", "Remove scratches & creases", "Paints out scratches, creases, tears, dust and spots.",
         "repair", edit=True, strength=False, prompt="optional", load_s=15, rate_mp=20),
    Tool("heavy", "Heavy restore (SUPIR)", "For badly damaged photos. Slow; re-draws detail.", "repair",
         "photo_supir", cap_mp=SUPIR_MP, multiple=64, prompt="optional", noncommercial=True, load_s=40, rate_mp=40,
         licence="Non-commercial only (SUPIR weights); SDXL base: CreativeML Open RAIL++-M"),
    Tool("jpeg", "Remove JPEG artefacts", "Blocky edges and colour smears from heavy compression.", "clean",
         "photo_model_1x", variants={"fbcnn": ("FBCNN", "1x_fbcnn_color.pth")}, default_variant="fbcnn",
         load_s=3, rate_mp=1.5),
    Tool("denoise", "Denoise", "Grain and sensor noise from dark or high-ISO shots.", "clean", "photo_model_1x",
         variants={"strong": ("Strong · NAFNet", "1x_NAFNet-SIDD-width64.pth"),
                   "gentle": ("Gentle · SCUNet", "1x_scunet_color_real_psnr.pth")},
         default_variant="strong", load_s=3, rate_mp=2.0),
    Tool("deblur", "Deblur · motion", "Camera shake and motion blur (not out-of-focus).", "clean", "photo_model_1x",
         variants={"gopro": ("NAFNet", "1x_NAFNet-GoPro-width64.pth")}, default_variant="gopro", load_s=3, rate_mp=2.0),
    Tool("faces", "Restore faces", "Sharper, cleaner faces. The rest of the picture is untouched.", "faces",
         "photo_face_restore", load_s=5, rate_mp=0.5),
    Tool("colourise", "Colourise", "Brings black & white, sepia and faded prints back to colour.", "colour",
         "photo_colourise", variants={"natural": ("Natural", "ddcolor_modelscope.pth"),
                                      "vivid": ("Vivid", "ddcolor_artistic.pth")},
         default_variant="natural", load_s=8, rate_mp=0.5),
    Tool("deyellow", "De-yellow", "Cools down a yellowed or sepia cast.", "colour", effect="deyellow"),
    Tool("polish", "Final polish", "Light levels and sharpening over the restored picture.", "finish",
         effect="auto_restore"),
    Tool("fix", "Prompted fix", "Describe what to change: “remove the stain on his collar”.", "repair",
         edit=True, strength=False, prompt="required", load_s=15, rate_mp=20),
    Tool("stylise", "Stylise", "Re-draw the photo as hand-painted or cinematic anime.", "style", edit=True,
         variants={"painterly": ("Hand-painted anime", "painterly"), "cinematic": ("Cinematic anime", "cinematic")},
         default_variant="painterly", strength=False, load_s=15, rate_mp=20),
    # cut-out: masks on the GPU, everything else on the CPU
    Tool("cutout", "Remove background", "Finds the subject and makes everything else transparent.", "cutout",
         "photo_cutout_mask", strength=False, load_s=4, rate_mp=0.5),
    Tool("select", "Select by words or clicks", "Pick what to keep or remove: “the dog”, or click on it.",
         "cutout", "photo_sam_mask", strength=False, load_s=8, rate_mp=0.5),
    Tool("background", "New background from a prompt", "Paints a new scene behind the cut-out subject.",
         "cutout", edit=True, strength=False, prompt="required", load_s=15, rate_mp=20),
)}

SCRATCH_PROMPT = ("Restore this old photograph: remove every scratch, crease, fold line, tear, dust speck, stain "
                  "and spot. Keep the people, faces, expressions, clothing, composition, grain and colours "
                  "exactly the same; change nothing else.")
STYLE_PROMPTS = {
    "painterly": ("Redraw this photo as a hand-painted anime film still: soft watercolour skies, warm painted "
                  "backgrounds, clean expressive character line art. Keep the same people, poses and composition."),
    "cinematic": ("Redraw this photo as a cinematic anime film still: crisp detailed backgrounds, dramatic sky and "
                  "light, luminous colour. Keep the same people, poses and composition."),
}
BACKGROUND_PROMPT = ("Replace the background with: {text}. Keep the main subject exactly as it is, the same "
                     "position, size, pose, edges and lighting direction; match the new scene's light to it.")
FIX_PREFIX = "Edit this photo: {text}. Change only that; keep everything else exactly the same."


class RestoreError(ValueError):
    pass


def settings_allow(tool: Tool) -> bool:
    return not tool.noncommercial or get_settings().photo_allow_noncommercial


# ---------------------------------------------------------------- availability

def _edit_template() -> str:
    from app import models_catalog as mc

    return mc.default_for("edit").template


def template_of(tool: Tool) -> str | None:
    if tool.edit:
        return _edit_template()
    return tool.template


def tool_options(object_info: dict | None, *, driver: str, error: str | None = None) -> list[dict]:
    from app.upscale import _model_options
    from app.workflows import check_template

    have_1x = set(_model_options(object_info, "UpscaleModelLoader", "model_name")) if object_info else set()
    have_ddc = set()
    if object_info:
        spec = (object_info.get("DDColor_Colorize", {}).get("input", {}).get("required", {}) or {}).get("checkpoint")
        have_ddc = set(spec[0]) if spec and isinstance(spec[0], list) else set()
    have_unet = set(_model_options(object_info, "UNETLoader", "unet_name")) if object_info else set()
    out = []
    for t in TOOLS.values():
        item = {"id": t.id, "label": t.label, "hint": t.hint, "group": t.group, "gpu": t.effect is None,
                "strength": t.strength, "prompt": t.prompt, "noncommercial": t.noncommercial, "licence": t.licence,
                "variants": [{"id": k, "label": v[0]} for k, v in t.variants.items()],
                "default_variant": t.default_variant, "available": True, "reason": None}
        problems = []
        if not settings_allow(t):
            problems.append("not available on this server")
        tpl = template_of(t)
        if tpl and driver != "mock":
            if object_info is None:
                problems.append(f"ComfyUI isn't reachable ({error or 'no answer'})")
            else:
                res = check_template(tpl, object_info)
                problems += ([f"missing nodes: {', '.join(res['missing_nodes'])}"] if res["missing_nodes"] else [])
                # the model is swapped per variant, so the template's literal default doesn't decide
                if tpl not in ("photo_model_1x", "photo_colourise", "image_upscale_seedvr2"):
                    problems += ([f"missing models: {', '.join(res['missing_models'])}"] if res["missing_models"] else [])
                pool = {"photo_model_1x": have_1x, "photo_colourise": have_ddc,
                        "image_upscale_seedvr2": have_unet}.get(tpl)
                if pool is not None and t.variants:
                    ok = [k for k, v in t.variants.items() if v[1] in pool]
                    item["variants"] = [x for x in item["variants"] if x["id"] in ok]
                    if not ok:
                        problems.append("missing models: " + ", ".join(v[1] for v in t.variants.values()))
                    elif item["default_variant"] not in ok:
                        item["default_variant"] = ok[0]
        if problems:
            item.update(available=False, reason="; ".join(problems))
        out.append(item)
    return out


# ---------------------------------------------------------------- sizes

def _fit_mp(w: int, h: int, mp: float, multiple: int) -> tuple[int, int]:
    s = min(1.0, math.sqrt(mp * 1e6 / (w * h)))
    return max(multiple, int(w * s) // multiple * multiple), max(multiple, int(h * s) // multiple * multiple)


def _edit_size(w: int, h: int) -> tuple[int, int]:
    # edits always run at ~1 MP, up or down
    s = math.sqrt(FIX_MP * 1e6 / (w * h))
    return max(256, int(w * s) // 16 * 16), max(256, int(h * s) // 16 * 16)


def proc_size(tool: Tool, w: int, h: int) -> tuple[int, int]:
    if tool.edit:
        return _edit_size(w, h)
    if tool.id == "repair":
        return _fit_mp(w, h, get_settings().image_upscale_max_mp, 2)
    if tool.cap_mp:
        return _fit_mp(w, h, tool.cap_mp, tool.multiple)
    return w, h


def ddcolor_size(w: int, h: int) -> int:
    # NoorViz's adaptive input: more pixels, a larger colour map
    mp = w * h / 1e6
    return 1024 if mp >= 6 else 768 if mp >= 2 else 512


def estimate(tool: Tool, w: int, h: int) -> int:
    if tool.effect:
        return 0
    pw, ph = proc_size(tool, w, h)
    return round(tool.load_s + tool.rate_mp * pw * ph / 1e6)


# ---------------------------------------------------------------- sources

def _image_size(g: Generation) -> tuple[int, int]:
    path = svc.check_image(g)
    with Image.open(path) as im:
        w, h = im.size
        if (im.getexif() or {}).get(0x0112) in (5, 6, 7, 8):
            w, h = h, w
    if w * h > MAX_INPUT_MP * 1e6:
        raise HTTPException(413, f"This image is {w}×{h}; restore tools take up to {MAX_INPUT_MP:.0f} MP")
    return w, h


def cutout_base(db: Session, g: Generation) -> tuple[Generation, str | None]:
    """The un-cut picture behind a cut-out version, and its current mask (or None)."""
    c = (g.params or {}).get("cutout") or {}
    if c.get("base_id") and c.get("mask_file"):
        base = db.get(Generation, c["base_id"])
        if base is not None and base.workspace_id == g.workspace_id and base.file_path:
            return base, c["mask_file"]
    return g, None


# ---------------------------------------------------------------- queueing one tool

def _variant(tool: Tool, value: str | None) -> str | None:
    if not tool.variants:
        return None
    v = value or tool.default_variant
    if v not in tool.variants:
        raise RestoreError(f"{tool.label} has no '{value}' option (expected {', '.join(tool.variants)})")
    return v


def _points(raw, size: tuple[int, int]) -> str | None:
    # 0..1 positions from the canvas -> source pixels, as SAM3_Detect wants them
    if not raw:
        return None
    import json

    w, h = size
    pts = [{"x": int(round(min(1.0, max(0.0, float(p["x"]))) * (w - 1))),
            "y": int(round(min(1.0, max(0.0, float(p["y"]))) * (h - 1)))} for p in raw[:24]]
    return json.dumps(pts)


def build_restore(db: Session, source: Generation, tool_id: str, opts: dict) -> tuple[dict, str]:
    """(params for the new version, job message)."""
    tool = TOOLS.get(tool_id)
    if tool is None:
        raise RestoreError(f"Unknown tool '{tool_id}'")
    if not settings_allow(tool):
        raise RestoreError(f"{tool.label} isn't available on this server right now.")
    if tool.effect:
        raise RestoreError(f"{tool.label} runs as a quick effect")

    base, mask = (cutout_base(db, source) if tool.group == "cutout" else (source, None))
    w, h = _image_size(base)
    pw, ph = proc_size(tool, w, h)
    variant = _variant(tool, opts.get("variant"))
    strength = float(opts.get("strength", 1.0) if tool.strength else 1.0)
    if not 0.05 <= strength <= 1.0:
        raise RestoreError("Strength must be between 5 and 100%")
    text = (opts.get("prompt") or "").strip()[:PROMPT_MAX]
    if tool.prompt == "required" and not text:
        raise RestoreError(f"{tool.label} needs a short description")

    inputs: dict = {}
    if tool.template == "photo_model_1x":
        inputs["model"] = tool.variants[variant][1]
    elif tool.id == "colourise":
        inputs.update(checkpoint=tool.variants[variant][1], model_size=ddcolor_size(w, h))
    elif tool.id == "repair":
        inputs.update(model=tool.variants[variant][1], width=pw, height=ph)
    elif tool.id == "heavy":
        inputs.update(width=pw, height=ph)
        if text:
            inputs["prompt"] = text
    elif tool.edit:
        if tool.id == "scratches":
            prompt = SCRATCH_PROMPT + (f" Also: {text}." if text else "")
        elif tool.id == "stylise":
            prompt = STYLE_PROMPTS[variant] + (f" {text}" if text else "")
        elif tool.id == "background":
            if not mask:
                raise RestoreError("Remove the background first, then paint a new one behind the subject")
            prompt = BACKGROUND_PROMPT.format(text=text)
        else:
            prompt = FIX_PREFIX.format(text=text.rstrip("."))
        inputs.update(prompt=prompt, width=pw, height=ph, template=_edit_template())

    r = {"tool": tool.id, "variant": variant, "strength": round(strength, 3), "template": template_of(tool),
         "inputs": inputs, "source_id": base.id, "source_file": base.file_path, "size": [w, h], "proc": [pw, ph],
         "user_prompt": text or None}
    if tool.id == "colourise":
        # sepia and yellowed prints colourise better from a clean grey (NoorViz did the same)
        a = analysis.analyse(svc.source_rgb(base, 512), (w, h))
        r["grey_first"] = bool(a["is_grayscale"] or a["color_cast"] in ("yellow", "red") or _is_monotone(base))
    params = {"restore": r, "size": [w, h]}

    if tool.group == "cutout":
        op = opts.get("op") or ("replace" if tool.id == "cutout" else "add" if mask else "replace")
        if op not in co.OPS:
            raise RestoreError(f"Unknown mask operation '{op}'")
        prev_c = (source.params or {}).get("cutout") or {}
        c = {"stage": "background" if tool.id == "background" else "mask", "op": op, "base_id": base.id,
             "prev_mask": mask, "edge": _edge(opts.get("edge") or prev_c.get("edge")),
             "background": prev_c.get("background") or {"type": "transparent"}}
        if tool.id == "select":
            words = (opts.get("words") or "").strip()[:PROMPT_MAX]
            include, exclude = _points(opts.get("include"), (w, h)), _points(opts.get("exclude"), (w, h))
            if not (words or include):
                raise RestoreError("Type what to select, or click on it")
            inputs.update(words=words or None, include=include, exclude=exclude)
            c["words"] = words or None
        if tool.id == "background":
            c["background"] = {"type": "generated", "prompt": text}
        params["cutout"] = c
    return params, f"Waiting for a worker · {tool.label}"


def _edge(raw) -> dict:
    raw = raw or {}
    f = max(0.0, min(20.0, float(raw.get("feather", 0.6) or 0)))
    return {"feather": round(f, 2), "shift": int(max(-10, min(10, int(raw.get("shift", 0) or 0))))}


def _is_monotone(g: Generation) -> bool:
    """Sepia, cyanotype, a faded tinted print: some colour, but all of it one hue."""
    rgb = svc.source_rgb(g, 256).astype(np.float32) / 255
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = mx - mn
    if float(sat.mean()) > 0.3:
        return False
    tinted = sat > 0.04
    if tinted.mean() < 0.2:
        return True  # practically grey
    r, gch, b = (rgb[..., i][tinted] for i in range(3))
    hue = np.arctan2(np.sqrt(3) * (gch - b), 2 * r - gch - b)
    spread = 1 - np.hypot(np.cos(hue).mean(), np.sin(hue).mean())  # circular variance, 0 = one hue
    return float(spread) < 0.03


def queue_tool(db: Session, source: Generation, tool_id: str, opts: dict, user_id: str | None,
               chain: dict | None = None, note: str | None = None) -> Job:
    svc.check_image(source)
    tool = TOOLS.get(tool_id)
    if tool is not None and tool.effect:
        job = svc.queue_effect(db, source, tool.effect, float(opts.get("strength", 1.0)), "png", 95, note, user_id)
        if chain is not None:
            g = db.get(Generation, job.generation_id)
            g.params = {**(g.params or {}), "chain": chain}
        return job
    params, message = build_restore(db, source, tool_id, opts)
    if chain is None:
        twin = svc._running_twin(db, source, "restore", params["restore"])
        if twin is not None:
            return twin
    params["created_by"] = {"user_id": user_id, "flow": "photo"}
    if chain is not None:
        params["chain"] = chain
    return svc._new_version(db, source, params, job_type=GENERATE_JOB, payload={}, message=message, note=note)


# ---------------------------------------------------------------- Smart Restore

def noise_sigma(rgb: np.ndarray) -> float:
    """Immerkær's fast noise estimate on a full-detail centre crop, in 0..255 grey levels."""
    h, w = rgb.shape[:2]
    c = min(768, h, w)
    y0, x0 = (h - c) // 2, (w - c) // 2
    g = rgb[y0:y0 + c, x0:x0 + c, :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    if g.shape[0] < 8:
        return 0.0
    k = (g[:-2, :-2] - 2 * g[:-2, 1:-1] + g[:-2, 2:] - 2 * g[1:-1, :-2] + 4 * g[1:-1, 1:-1] - 2 * g[1:-1, 2:]
         + g[2:, :-2] - 2 * g[2:, 1:-1] + g[2:, 2:])
    return float(math.sqrt(math.pi / 2) * np.abs(k).mean() / 6)


def _jpeg_heavy(g: Generation, w: int, h: int) -> bool:
    path = generation_file(g)
    if not path or path.suffix.lower() not in (".jpg", ".jpeg"):
        return False
    return path.stat().st_size / max(1, w * h) < 0.3  # under ~0.3 bytes/pixel the blocks start to show


def smart_plan(db: Session, g: Generation, object_info: dict | None = None, driver: str = "comfy") -> dict:
    w, h = _image_size(g)
    proxy = svc.load_source(g).proxy
    rgb = np.asarray(proxy.convert("RGB"))
    a = analysis.analyse(rgb, (w, h))
    a["noise"] = round(noise_sigma(rgb), 2)
    a["is_monotone"] = bool(not a["is_grayscale"] and _is_monotone(g))
    mono = a["is_grayscale"] or a["is_monotone"]
    portrait = a["aspect_ratio"] < 0.85
    landscape = a["aspect_ratio"] > 1.3
    avail = {t["id"]: t for t in tool_options(object_info, driver=driver)} if object_info is not None or \
        driver == "mock" else {}

    steps: list[dict] = []

    def add(tool: str, reason: str, on: bool = True, variant: str | None = None, strength: float = 1.0):
        t = TOOLS[tool]
        info = avail.get(tool)
        ok = info["available"] if info else True
        steps.append({"id": f"s{len(steps) + 1}", "tool": tool, "label": t.label, "reason": reason,
                      "on": bool(on and ok), "available": ok, "unavailable_reason": info and info["reason"],
                      "variant": variant or t.default_variant, "strength": strength,
                      "est_gpu_s": estimate(t, w, h)})

    if _jpeg_heavy(g, w, h):
        add("jpeg", "Heavily compressed JPEG: blocky edges likely")
    damaged, blurry = a["is_likely_damaged"], a["is_likely_blurry"]
    if damaged:
        add("scratches", "Fading and edge wear consistent with an old, handled print", on=True)
        add("repair", "Repair the fading and wear while staying true to the original")
    elif blurry:
        add("repair", f"Sharpness {a['sharpness']} is low: a faithful repair recovers detail")
    if a["noise"] > 4.0 and not damaged:
        add("denoise", f"Noise level {a['noise']} (grain from a dark or high-ISO shot)",
            variant="gentle" if a["noise"] < 7 else "strong")
    if blurry and a["sharpness"] < 0.4 and not damaged:
        add("deblur", f"Sharpness {a['sharpness']}: below the motion-blur threshold", on=False)
    if mono:
        # NoorViz: landscapes vivid, people natural
        add("colourise", "Black & white or sepia print" + (" · landscape" if landscape else ""),
            variant="vivid" if landscape else "natural")
    elif a["color_cast"] == "yellow" or (a["is_grayscale"] and 110 < a["brightness"] < 170):
        add("deyellow", f"Yellow cast {round(a['cast_strength'] * 100)}%")
    # GFPGAN leaves a faceless picture alone, so it runs as a precaution on people-shaped photos
    if damaged or blurry or mono or portrait:
        add("faces", "Portrait-shaped or old photo: faces benefit most" if portrait else
            "Runs as a precaution: pictures without faces are left alone", on=not landscape)
    if steps:
        add("polish", "Light levels and sharpening over the restored result", strength=0.5)
    return {"analysis": a, "steps": steps, "source": {"width": w, "height": h},
            "message": None if steps else ("This photo looks clean already. Smart Restore found nothing worth "
                                           "doing; try a single tool below for a specific look.")}


def queue_smart(db: Session, source: Generation, steps: list[dict], user_id: str | None) -> dict:
    picked = [s for s in steps if s.get("on", True)]
    if not picked:
        raise RestoreError("Pick at least one step")
    for s in picked:
        if s.get("tool") not in TOOLS or TOOLS[s["tool"]].group == "cutout":
            raise RestoreError(f"'{s.get('tool')}' can't be a Smart Restore step")
    chain = {"id": new_id(), "user_id": user_id, "index": 0, "started": time.time(),
             "steps": [{"tool": s["tool"], "variant": s.get("variant"), "strength": s.get("strength", 1.0),
                        "label": TOOLS[s["tool"]].label} for s in picked]}
    job = queue_tool(db, source, picked[0]["tool"], picked[0], user_id, chain=chain,
                     note=f"Smart Restore 1/{len(picked)}")
    return {"chain_id": chain["id"], "steps": chain["steps"], "first_job_id": job.id}


def _queue_next(db: Session, gen: Generation, last_good: Generation) -> None:
    chain = (gen.params or {}).get("chain") or {}
    steps = chain.get("steps") or []
    skipped = []
    for nxt in range(int(chain.get("index", 0)) + 1, len(steps)):
        step = steps[nxt]
        try:
            queue_tool(db, last_good, step["tool"], step, chain.get("user_id"), chain={**chain, "index": nxt},
                       note=f"Smart Restore {nxt + 1}/{len(steps)}")
            break
        except (RestoreError, HTTPException) as e:
            # e.g. the tool went missing from ComfyUI mid-chain: note it and try the step after
            log.warning("smart restore step %s skipped: %s", step["tool"], e)
            skipped.append({"tool": step["tool"], "error": str(getattr(e, "detail", e))})
    if skipped:
        gen.params = {**(gen.params or {}), "chain_skipped": skipped}


def on_job_finished(db: Session, job: Job) -> None:
    if job.type not in (GENERATE_JOB, svc.RENDER_JOB) or not job.generation_id:
        return
    gen = db.get(Generation, job.generation_id)
    if gen is None or not (gen.params or {}).get("chain"):
        return
    if job.status == "cancelled":
        return  # cancelling a step cancels the rest
    if job.status == "done" and gen.status in svc.FINISHED:
        last_good = gen
    else:
        # NoorViz kept going after a failed step; so do we, from the version this step started from
        last_good = db.get(Generation, gen.parent_id) if gen.parent_id else None
        if last_good is None or last_good.status not in svc.FINISHED:
            return
    _queue_next(db, gen, last_good)


# ---------------------------------------------------------------- finishing on the CPU

def _src_path(gen: Generation) -> Path:
    r = (gen.params or {}).get("restore") or {}
    p = get_settings().data_dir / (r.get("source_file") or "")
    if not p.is_file():
        raise RuntimeError("The source image of this restore is gone")
    return p


def _open_rgb(path: Path) -> tuple[Image.Image, Image.Image | None]:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        alpha = im.getchannel("A") if im.mode in ("RGBA", "LA") else None
        return im.convert("RGB"), alpha


def _changed_mask(src: Image.Image, edited: Image.Image) -> Image.Image:
    """Where a ~1 MP edit actually changed something, feathered; the rest keeps full-res source detail."""
    w, h = edited.size
    a = np.asarray(src.resize((w, h), Image.LANCZOS), dtype=np.float32)
    b = np.asarray(edited, dtype=np.float32)
    # edit models drift the overall colour a little; take that out before diffing
    b_adj = b - (b.mean((0, 1)) - a.mean((0, 1)))
    d = np.abs(a - b_adj).max(-1)
    m = Image.fromarray(np.clip((d - 18) * 6, 0, 255).astype(np.uint8), "L")
    m = m.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MaxFilter(9))
    return m.filter(ImageFilter.GaussianBlur(max(2.0, max(w, h) / 300)))


def finish(gen: Generation, out_path: Path) -> dict:
    """Called by the worker after the driver wrote ComfyUI's result to out_path. Rewrites out_path."""
    p = gen.params or {}
    r = p.get("restore") or {}
    c = p.get("cutout")
    tool = TOOLS.get(r.get("tool") or "")
    if tool is None:
        return {}
    for tmp_grey in (get_settings().data_dir / "tmp").glob(f"grey-{r.get('source_id')}-*.png"):
        tmp_grey.unlink(missing_ok=True)
    src, src_alpha = _open_rgb(_src_path(gen))
    size = src.size
    with Image.open(out_path) as im:
        res = im.convert("RGB")
    extra: dict = {}

    if c and c.get("stage") == "mask":
        new = res.convert("L").resize(size, Image.BILINEAR) if res.size != size else res.convert("L")
        prev = co.load_mask(get_settings().data_dir / c["prev_mask"], size) if c.get("prev_mask") else None
        if prev is None and c.get("op") == "subtract":
            prev = Image.new("L", size, 255)  # "remove this" on an uncut photo: everything else stays
        mask = co.combine(prev, new, c.get("op") or "replace")
        mabs, mrel = svc.out_path(gen, ".mask.png")
        mask.save(mabs, "PNG", optimize=False, compress_level=3)
        bg_path = get_settings().data_dir / c["background"]["file"] if c.get("background", {}).get("file") else None
        bg = co.background_for(src, c.get("background") or {}, bg_path)
        out = co.compose(src, co.refine_edge(mask, **c.get("edge", {})), bg)
        extra = {"mask_file": mrel, "coverage": round(co.coverage(mask), 4)}
    elif c and c.get("stage") == "background":
        mask = co.load_mask(get_settings().data_dir / c["prev_mask"], size)
        bg_img = res.resize(size, Image.LANCZOS)
        babs, brel = svc.out_path(gen, ".bg.png")
        bg_img.save(babs, "PNG", compress_level=3)
        out = co.compose(src, co.refine_edge(mask, **c.get("edge", {})), bg_img)
        extra = {"mask_file": c["prev_mask"], "background": {**c["background"], "file": brel}}
    elif tool.id == "fix" or tool.id == "scratches":
        m = _changed_mask(src, res)
        cover = co.coverage(m)
        up = res.resize(size, Image.LANCZOS)
        # a fix that touched most of the frame is a re-draw anyway; don't stitch it
        out = up if cover > 0.6 else Image.composite(up, src, m.resize(size, Image.BILINEAR))
        extra = {"changed": round(cover, 4)}
    else:
        if res.size != size:
            res = res.resize(size, Image.LANCZOS)
        s = float(r.get("strength") or 1.0)
        out = res if s >= 0.999 else Image.blend(src, res, s)

    if src_alpha is not None and out.mode == "RGB" and not c:
        out.putalpha(src_alpha)
    tmp = out_path.with_name(out_path.name + ".tmp")
    out.save(tmp, "PNG", compress_level=4)
    tmp.replace(out_path)
    if c:
        gen.params = {**p, "cutout": {**c, **extra}}
    elif extra:
        gen.params = {**p, "restore": {**r, **extra}}
    return {"width": out.width, "height": out.height}


def needs_finish(params: dict) -> bool:
    return bool((params or {}).get("restore"))


def comfy_input(r: dict, path: Path) -> Path:
    """The file ComfyUI gets. Colourise wants a clean, stretched grey for sepia/yellowed prints."""
    if not r.get("grey_first"):
        return path
    tmp = get_settings().data_dir / "tmp" / f"grey-{r.get('source_id')}-{int(time.time() * 1000)}.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        g = ImageOps.autocontrast(ImageOps.exif_transpose(im).convert("L"), cutoff=0.5)
    g.convert("RGB").save(tmp, "PNG", compress_level=1)
    return tmp


# ---------------------------------------------------------------- cut-out background / edge (CPU job)

def queue_background(db: Session, source: Generation, background: dict, edge: dict | None,
                     user_id: str | None) -> Job:
    svc.check_image(source)
    base, mask = cutout_base(db, source)
    if not mask:
        raise RestoreError("Remove the background first")
    bg = dict(background or {"type": "transparent"})
    kind = bg.get("type") or "transparent"
    if kind == "colour":
        co.parse_colour(bg.get("colour", ""))
    elif kind == "image":
        from app.services import owned_source

        pic = owned_source(db, source.workspace_id, bg.get("generation_id") or "")
        if not (pic.media_type or "").startswith("image/") or not pic.file_path:
            raise RestoreError("The background has to be a finished image")
        bg = {"type": "image", "generation_id": pic.id, "file": pic.file_path}
    elif kind == "generated":
        prev = ((source.params or {}).get("cutout") or {}).get("background") or {}
        if not prev.get("file"):
            raise RestoreError("This cut-out has no painted background to keep")
        bg = prev
    elif kind == "blur":
        bg = {"type": "blur", "radius": max(2.0, min(60.0, float(bg.get("radius") or 18)))}
    elif kind != "transparent":
        raise RestoreError(f"Unknown background '{kind}'")
    c = {"stage": "compose", "base_id": base.id, "mask_file": mask, "edge": _edge(edge),
         "background": bg if kind != "transparent" else {"type": "transparent"},
         "coverage": ((source.params or {}).get("cutout") or {}).get("coverage")}
    twin = svc._running_twin(db, source, "cutout", c)
    if twin is not None:
        return twin
    label = {"transparent": "Transparent", "colour": "Colour", "image": "Picture", "blur": "Blurred",
             "generated": "Edge"}[kind]
    return svc._new_version(db, source, {"cutout": c, "created_by": {"user_id": user_id, "flow": "photo"}},
                            job_type=svc.RENDER_JOB, payload={"mode": "cutout"},
                            message=f"Waiting for a worker · Background: {label}")


def render_cutout(ctx, gen: Generation) -> dict:
    db = ctx.db
    c = gen.params["cutout"]
    base = db.get(Generation, c["base_id"])
    path = generation_file(base) if base else None
    if not path or not path.is_file():
        raise svc.PhotoError("The original picture of this cut-out is gone")
    gen.status = "generating"
    db.commit()
    t0 = time.monotonic()
    ctx.progress(0.1, "Reading the image")
    src, _ = _open_rgb(path)
    mask = co.load_mask(get_settings().data_dir / c["mask_file"], src.size)
    bg_file = c["background"].get("file")
    ctx.progress(0.4, "Compositing")
    out = co.compose(src, co.refine_edge(mask, **c["edge"]),
                     co.background_for(src, c["background"], get_settings().data_dir / bg_file if bg_file else None))
    abs_path, rel = svc.out_path(gen, ".png")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = abs_path.with_name(abs_path.name + ".tmp")
    out.save(tmp, "PNG", compress_level=4)
    tmp.replace(abs_path)
    db.refresh(gen)
    gen.params = {**(gen.params or {}), "size": [out.width, out.height]}
    gen.file_path = rel
    gen.media_type = "image/png"
    gen.status = "ready"
    return {"file_path": rel, "media_type": "image/png", "width": out.width, "height": out.height,
            "seconds": round(time.monotonic() - t0, 3)}


# ---------------------------------------------------------------- describe (vision LLM)

def describe(g: Generation) -> dict:
    from pydantic import BaseModel

    from app.llm import LLMError, chat_sync
    from app.vision import image_b64

    class Described(BaseModel):
        caption: str
        details: str = ""
        defects: list[str] = []
        suggested_tools: list[str] = []

    path = svc.check_image(g)
    tools = ", ".join(t for t in TOOLS if TOOLS[t].group not in ("cutout", "style"))
    msgs = [
        {"role": "system", "content": "You describe photographs for a photo editor. Answer only with JSON."},
        {"role": "user", "images": [image_b64(path)], "content": (
            "Describe this photo: a one-sentence caption, then 2-4 sentences on subject, setting, light and era. "
            "List visible defects (scratches, creases, stains, fading, colour cast, blur, noise, JPEG blocks, "
            f"damaged faces). Suggest restore tools from this list only: {tools}.\n"
            'Reply as JSON {"caption": "", "details": "", "defects": [], "suggested_tools": []}.')},
    ]
    try:
        res = chat_sync("vision", msgs, schema=Described, temperature=0.2, max_tokens=500,
                        timeout=get_settings().vision_timeout_s)
    except LLMError as e:
        raise HTTPException(503, f"The vision model isn't available: {e}") from None
    d = res.data
    return {"caption": d.caption.strip(), "details": d.details.strip(), "defects": d.defects[:10],
            "suggested_tools": [t for t in d.suggested_tools if t in TOOLS][:6]}
