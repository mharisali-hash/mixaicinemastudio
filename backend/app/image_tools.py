"""Image tools (contract v30): single-purpose ComfyUI graphs behind some Edit Image templates.

Line art, depth and edge maps (comfyui_controlnet_aux), mask-exact object removal (LaMa), relighting
(IC-Light v1) and virtual try-on (CatVTON). A template names its tool in image.json; when the tool can't
run (switched off, not installed, or no mask for LaMa) the templates that allow it fall back to the
ordinary Qwen-Image-Edit instruction, so turning IMAGE_ALLOW_NONCOMMERCIAL off never leaves a dead card.
"""
import base64
import binascii
import io
import math
import re
from dataclasses import dataclass

from fastapi import HTTPException
from PIL import Image
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Generation, new_id

ANNOTATORS = ("Unverified: lllyasviel/Annotators is marked licence 'other' (upstream Informative Drawings is MIT); "
              "treated as non-commercial until confirmed")


@dataclass(frozen=True)
class ImageTool:
    id: str
    label: str
    engine: str  # what the UI says it runs on
    template: str
    licence: str
    noncommercial: bool = False
    # Qwen-Image-Edit can stand in with the template's instruction when this tool can't run
    fallback: bool = False
    needs_mask: bool = False
    generative: bool = False  # samples (seed and count matter); the others give the same picture every time
    cap_mp: float = 4.0
    multiple: int = 8
    est_seconds: int = 6


TOOLS: dict[str, ImageTool] = {t.id: t for t in (
    ImageTool("lineart", "Line art", "ControlNet line art", "image_lineart", ANNOTATORS, noncommercial=True,
              fallback=True),
    ImageTool("lineart_anime", "Anime line art", "ControlNet anime line art", "image_lineart_anime",
              "Unverified: netG.pth from lllyasviel/Annotators (licence 'other'); treated as non-commercial",
              noncommercial=True, fallback=True),
    ImageTool("depth", "Depth map", "Depth Anything V2 Small", "image_depth", "Apache-2.0 (Depth Anything V2 Small)"),
    ImageTool("canny", "Edge map", "Canny edges", "image_canny", "No model weights (OpenCV Canny, Apache-2.0)",
              est_seconds=3),
    ImageTool("softedge", "Soft edge map", "HED soft edges", "image_softedge",
              "Unverified: ControlNetHED.pth from lllyasviel/Annotators (licence 'other'); treated as non-commercial",
              noncommercial=True),
    ImageTool("inpaint", "Remove with a mask", "LaMa", "image_lama_inpaint", "Apache-2.0 (LaMa big-lama)",
              fallback=True, needs_mask=True, cap_mp=4.2, est_seconds=8),
    # the engine name reaches the page, so it says what it does rather than "SAM + LaMa"
    ImageTool("remove", "Remove by words", "Remove by words", "image_remove_words",
              "SAM Licence (SAM 3.1, Meta; unconfirmed for commercial use) + Apache-2.0 (LaMa big-lama)",
              cap_mp=4.2, est_seconds=12),
    ImageTool("relight", "Relight", "IC-Light v1", "image_iclight",
              "Apache-2.0 (IC-Light v1); base Realistic Vision 5.1: CreativeML OpenRAIL-M", fallback=True,
              generative=True, cap_mp=0.79, multiple=64, est_seconds=25),
    ImageTool("tryon", "Virtual try-on", "CatVTON", "image_catvton",
              "CC BY-NC-SA 4.0 (CatVTON weights): non-commercial only", noncommercial=True, generative=True,
              cap_mp=0.79, multiple=16, est_seconds=40),
)}
TEMPLATES = frozenset(t.template for t in TOOLS.values())

LIGHTS = {"left": "Left Light", "right": "Right Light", "top": "Top Light", "bottom": "Bottom Light"}
CLOTH_TYPES = ("upper", "lower", "overall")
MAX_MASK_BYTES = 8 * 1024 * 1024


class ToolError(ValueError):
    pass


def remove_words(opts: dict) -> str:
    words = " ".join(str((opts or {}).get("words") or "").split())[:200]
    if not words:
        raise ToolError("Say what to remove, e.g. men, the dog, the text")
    return words


def allowed(tool: ImageTool) -> bool:
    return not tool.noncommercial or get_settings().image_allow_noncommercial


def _problems(tool: ImageTool, info: dict) -> str | None:
    from app import models_catalog as mc
    from app.workflows import check_template

    res = check_template(tool.template, info)
    return None if res["ok"] else "; ".join(mc._problems(res))


def unavailable(tool: ImageTool) -> str | None:
    """Why the tool can't run: switched off, or the last /object_info says it isn't installed.
    None when fine or not known (a missing node then fails the job, like the models do)."""
    if not allowed(tool):
        return f"{tool.engine} isn't available on this server"
    if get_settings().gen_driver == "mock":
        return None
    from app.models_catalog import _cached_info

    info = _cached_info()
    return None if info is None else _problems(tool, info)


def options(info: dict | None, *, driver: str, error: str | None = None) -> list[dict]:
    """GET /images/tools. Switched-off tools are left out, as hidden audio models are."""
    out = []
    for t in TOOLS.values():
        if not allowed(t):
            continue
        reason = None
        if driver != "mock":
            reason = f"ComfyUI isn't reachable ({error or 'no answer'})" if info is None else _problems(t, info)
        out.append({"id": t.id, "label": t.label, "engine": t.engine, "licence": t.licence,
                    "noncommercial": t.noncommercial, "fallback": t.fallback, "needs_mask": t.needs_mask,
                    "available": reason is None, "reason": reason, "est_seconds": t.est_seconds})
    return out


# ---------------------------------------------------------------- templates

def template_tool(t: dict) -> dict | None:
    """What a prompt template card says about its tool (TemplateOut.tool)."""
    tool = TOOLS.get(t.get("tool") or "")
    if tool is None:
        return None
    return {"id": tool.id, "label": tool.label, "engine": tool.engine, "licence": tool.licence,
            "noncommercial": tool.noncommercial, "fallback": tool.fallback, "needs_mask": tool.needs_mask}


def template_visible(t: dict) -> bool:
    # a tool-only card (try-on, depth) with nothing to fall back on disappears with its switch
    tool = TOOLS.get(t.get("tool") or "")
    return tool is None or tool.fallback or allowed(tool)


def slot_values(scaffold: str, text: str) -> dict[str, str]:
    """Read back what the user put in each [slot] of a scaffold. {} when they've rewritten it."""
    parts = re.split(r"\[([^\[\]]{1,60})\]", scaffold)
    if len(parts) == 1:
        return {}
    pattern, names = "", []
    for i, part in enumerate(parts):
        if i % 2:
            names.append(part)
            pattern += f"(?P<s{len(names)}>.+?)"
        else:
            pattern += re.escape(part)
    m = re.fullmatch(pattern, text.strip(), re.S)
    if not m:
        return {}
    return {name: m.group(f"s{i}").strip() for i, name in enumerate(names, 1)}


# ---------------------------------------------------------------- sizes and masks

def oriented_size(g: Generation) -> tuple[int, int]:
    # ComfyUI's LoadImage applies EXIF rotation, so the mask has to match the turned picture
    path = get_settings().data_dir / (g.file_path or "")
    with Image.open(path) as im:
        w, h = im.size
        if (im.getexif() or {}).get(0x0112) in (5, 6, 7, 8):
            w, h = h, w
    return w, h


def work_size(tool: ImageTool, w: int, h: int) -> tuple[int, int]:
    m = tool.multiple
    if tool.generative:
        s = math.sqrt(tool.cap_mp * 1e6 / (w * h))  # SD1.5 tools want their own size, up or down
    else:
        s = min(1.0, math.sqrt(tool.cap_mp * 1e6 / (w * h)))
    return max(m * 4, int(w * s) // m * m), max(m * 4, int(h * s) // m * m)


def decode_mask(data: str) -> Image.Image:
    head, _, body = data.partition(",")
    if not head.startswith("data:image/") or ";base64" not in head or not body:
        raise ToolError("The mask must be a PNG or WebP data URL")
    if len(body) > MAX_MASK_BYTES * 4 // 3 + 4:
        raise ToolError("The mask is too large")
    try:
        raw = base64.b64decode(body, validate=True)
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > 40e6:
                raise ToolError("The mask is too large")
            if im.mode in ("RGBA", "LA"):
                # a brush layer: painted where it isn't transparent
                return im.getchannel("A").copy()
            return im.convert("L")
    except (binascii.Error, OSError, ValueError, Image.DecompressionBombError):
        raise ToolError("The mask isn't a readable image") from None


def store_mask(mask: Image.Image, size: tuple[int, int], workspace_id: str) -> str:
    m = mask.resize(size, Image.BILINEAR) if mask.size != size else mask
    m = m.point(lambda v: 255 if v >= 128 else 0)
    hist = m.histogram()
    cover = hist[255] / max(1, size[0] * size[1])
    if cover < 0.0005:
        raise ToolError("The mask is empty: paint over what should go")
    if cover > 0.9:
        raise ToolError("The mask covers almost the whole picture; paint only what should go")
    rel = f"workspaces/{workspace_id}/media/masks/{new_id()}.png"
    path = get_settings().data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    m.convert("RGB").save(path, "PNG", optimize=False, compress_level=3)
    return rel


def has_selection(db: Session, g: Generation) -> bool:
    from app.photo.restore import cutout_base

    return bool(cutout_base(db, g)[1])


def selection_mask(db: Session, g: Generation) -> tuple[Generation, Image.Image] | None:
    """A Photo Studio Select / cut-out version: the uncut picture and its selection (white = selected)."""
    from app.photo import cutout as co
    from app.photo.restore import cutout_base

    base, mask_file = cutout_base(db, g)
    if not mask_file:
        return None
    path = get_settings().data_dir / mask_file
    if not path.is_file():
        return None
    return base, co.load_mask(path, oriented_size(base))


# ---------------------------------------------------------------- the request

def light_of(value) -> str | None:
    v = str(value or "none").strip().lower()
    if v == "none":
        return None
    if v not in LIGHTS:
        raise ToolError(f"Light must be one of: none, {', '.join(LIGHTS)}")
    return LIGHTS[v]


def cloth_of(opts: dict, slots: dict) -> str:
    want = str(opts.get("garment_type") or "").lower()
    if not want:
        text = (slots.get("garment type") or "").lower()
        want = "lower" if "lower" in text else "overall" if ("full" in text or "dress" in text) else "upper"
    if want not in CLOTH_TYPES:
        raise ToolError(f"Garment type must be one of: {', '.join(CLOTH_TYPES)}")
    return want


def pick_lineart(slots: dict) -> tuple[str, bool]:
    style = (slots.get("line style") or "").lower()
    if ("manga" in style or "anime" in style) and unavailable(TOOLS["lineart_anime"]) is None:
        return "lineart_anime", False
    return "lineart", "colouring" in style


def pick_edges(opts: dict, slots: dict) -> str:
    want = str(opts.get("edge") or slots.get("edge style") or "").lower()
    # soft edges ride on the Annotators weights; crisp Canny needs none, so it is what's left when they're off
    return "softedge" if "soft" in want and unavailable(TOOLS["softedge"]) is None else "canny"


def tool_for(template: dict | None, tool_id: str | None) -> ImageTool | None:
    tid = tool_id or (template or {}).get("tool")
    if not tid:
        return None
    tool = TOOLS.get(tid)
    if tool is None:
        raise HTTPException(422, f"Unknown image tool '{tid}' (choose one of: {', '.join(TOOLS)})")
    return tool


def build_params(db: Session, workspace_id: str, tool: ImageTool, template: dict | None, instruction: str,
                 sources: list[Generation], opts: dict, mask: str | None) -> dict:
    """params["image_tool"] for the driver, plus the work size. Raises ToolError for what the user can fix."""
    scaffold = ((template or {}).get("defaults") or {}).get("instruction_scaffold", "")
    slots = slot_values(scaffold, instruction) if scaffold else {}
    src = sources[0]
    rec: dict = {"tool": tool.id, "source_id": src.id}
    inputs: dict = {}

    if tool.id == "inpaint":
        picked = None if mask else selection_mask(db, src)
        if picked is not None:
            src, m = picked
            rec.update(source_id=src.id, mask_from="selection")
        elif mask:
            m = decode_mask(mask)
            rec["mask_from"] = "brush"
        else:
            raise ToolError("Paint over what should go, or pick a Photo Studio selection")
        size = work_size(tool, *oriented_size(src))
        rec["mask_file"] = store_mask(m, size, workspace_id)
    else:
        size = work_size(tool, *oriented_size(src))

    if tool.id in ("lineart", "lineart_anime"):
        tid, coarse = pick_lineart(slots) if tool.id == "lineart" else (tool.id, False)
        tool = TOOLS[tid]
        rec["tool"] = tid
        if coarse:
            inputs["coarse"] = "enable"  # fewer, bolder lines: a colouring page, not a pen drawing
        inputs["resolution"] = min(2048, max(512, min(size) // 64 * 64))
    elif tool.id in ("depth", "canny", "softedge"):
        if tool.id != "depth":
            tool = TOOLS[pick_edges(opts, slots)]
            rec["tool"] = tool.id
        inputs["resolution"] = min(2048, max(512, min(size) // 64 * 64))
        if tool.id == "canny":
            lo, hi = int(opts.get("low_threshold", 100)), int(opts.get("high_threshold", 200))
            if not 0 <= lo < hi <= 255:
                raise ToolError("Canny thresholds must satisfy 0 <= low < high <= 255")
            inputs.update(low_threshold=lo, high_threshold=hi)
    elif tool.id == "remove":
        inputs["words"] = remove_words(opts)
        try:
            inputs["threshold"] = round(max(0.1, min(0.9, float(opts.get("threshold", 0.5)))), 2)
            inputs["grow"] = max(0, min(64, int(opts.get("grow", 10))))
        except (TypeError, ValueError):
            raise ToolError("threshold and grow must be numbers") from None
    elif tool.id == "relight":
        light = light_of(opts.get("light"))
        inputs.update(light=light or "Left Light", denoise=0.9 if light else 1.0,
                      prompt=relight_prompt(template, slots, instruction))
        if len(sources) > 1:
            rec["background_id"] = sources[1].id
            inputs["iclight_model"] = "iclight_sd15_fbc.safetensors"
        rec["light"] = opts.get("light") or "none"
    elif tool.id == "tryon":
        if len(sources) < 2:
            raise ToolError("Virtual try-on needs the person and the garment pictures")
        rec["garment_id"] = sources[1].id
        inputs["cloth_type"] = cloth_of(opts, slots)

    rec.update(template=tool.template, inputs={**inputs, "width": size[0], "height": size[1]})
    return rec


def relight_prompt(template: dict | None, slots: dict, instruction: str) -> str:
    # SD1.5's text encoder reads ~75 tokens: the scene and the light, not the whole Qwen instruction
    tp = (template or {}).get("tool_prompt")
    if tp and slots:
        text = tp
        for k, v in slots.items():
            text = text.replace(f"[{k}]", v)
        if "[" not in text:
            return text
    return " ".join(instruction.split())[:300]


def nothing_removed(db: Session, gen: Generation, out_path) -> str | None:
    """Remove by words: when the words matched nothing, LaMa hands back the same picture. Say so instead of
    filing a copy that looks like a failed edit."""
    import numpy as np
    from PIL import ImageOps

    rec = (gen.params or {}).get("image_tool") or {}
    if rec.get("tool") != "remove":
        return None
    src = db.get(Generation, rec.get("source_id") or "")
    if src is None or not src.file_path:
        return None
    try:
        with Image.open(out_path) as o:
            out = np.asarray(o.convert("RGB"), dtype=np.int16)
        with Image.open(get_settings().data_dir / src.file_path) as s:
            # LoadImage turns by EXIF; the Fit resample is close enough to PIL's that only real edits stand out
            before = ImageOps.exif_transpose(s).convert("RGB").resize((out.shape[1], out.shape[0]), Image.LANCZOS)
    except OSError:
        return None
    diff = np.abs(out - np.asarray(before, dtype=np.int16)).max(axis=2)
    if diff.mean() < 1.5 and (diff > 40).mean() < 0.001:
        words = (rec.get("inputs") or {}).get("words") or "that"
        return f"Nothing matching “{words}” was found, so the picture wasn't changed"
    return None
