"""Magic prompt (UI polish P1): the creative LLM fills a short prompt out with visual detail.

Used by POST /prompts/enhance (the dock shows the result before running) and by the generators'
jobs when the client didn't enhance up front. Quoted text is the one thing it must never touch:
Qwen-Image renders it letter for letter, Urdu included.
"""
import asyncio
import logging
import re
import time
from collections import OrderedDict

from pydantic import BaseModel

from app.config import get_settings
from app.llm.client import LLMError, chat, chat_sync

log = logging.getLogger("mixai.enhance")

KINDS = ("image", "video", "quick", "edit")
WORD_CAP = {"image": 120, "video": 160, "quick": 160, "edit": 80}
SHORT_WORDS = 12
CACHE_TTL_S = 600.0
CACHE_MAX = 256
UNAVAILABLE = "enhancer unavailable"

# straight, curly, guillemets and CJK corner quotes; single quotes are left out (apostrophes)
_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|«([^»\n]+)»|「([^」\n]+)」')
_WORD = re.compile(r"\w+", re.U)

VISUAL_CUES = {
    "light", "lighting", "lit", "backlit", "shadow", "shadows", "glow", "glowing", "sunlight", "sunset", "sunrise",
    "dusk", "dawn", "golden", "neon", "candlelight", "moonlight", "fog", "haze", "mist", "rain", "camera", "lens",
    "shot", "closeup", "close", "wide", "macro", "angle", "aerial", "overhead", "portrait", "cinematic",
    "photoreal", "photorealistic", "photograph", "photo", "illustration", "painting", "watercolour", "watercolor",
    "render", "3d", "35mm", "50mm", "85mm", "bokeh", "depth", "focus", "colour", "color", "colours", "colors",
    "palette", "texture", "mood", "atmosphere", "composition", "background", "foreground", "studio", "film",
    "grain", "style", "tones", "pastel", "vibrant", "muted", "monochrome", "dolly", "pan", "tracking", "handheld",
    "zoom", "orbit", "crane", "slow", "motion", "sound", "ambient",
}


class Enhanced(BaseModel):
    prompt: str
    notes: str = ""


def quoted(text: str) -> list[str]:
    return [next(g for g in m.groups() if g is not None) for m in _QUOTED.finditer(text or "")]


def needs_enhance(prompt: str) -> bool:
    """Auto mode: short prompts, or longer ones without a single look/light/camera word."""
    words = _WORD.findall(_QUOTED.sub(" ", prompt or "").lower())
    if len(words) < SHORT_WORDS:
        return True
    return not any(w in VISUAL_CUES for w in words)


def _result(original: str, enhanced: str | None = None, notes: str | None = None) -> dict:
    enhanced = (enhanced or original).strip()
    out = {"enhanced": enhanced, "changed": enhanced != original.strip()}
    if notes:
        out["notes"] = notes
    return out


# ---------------------------------------------------------------- cache

_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()


def _cache_get(key: tuple) -> dict | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    if time.monotonic() - hit[0] > CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return dict(hit[1])


def _cache_put(key: tuple, value: dict) -> None:
    _cache[key] = (time.monotonic(), dict(value))
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------- the call

def messages(prompt: str, kind: str, *, model: str | None = None, style: str | None = None,
             brand_context: str = "") -> list[dict]:
    cap = WORD_CAP[kind]
    if kind == "image":
        what = ("an image model. Add concrete visual detail: subject, setting, composition, lens or medium, "
                "lighting, colour and texture.")
    elif kind == "video":
        what = ("a video model that also makes sound. Add the camera (shot size and one clear movement), how the "
                "subject moves over the clip, the light, and sound cues: ambience, effects, and any spoken line.")
    elif kind == "edit":
        what = ("an image editing model that changes an existing picture. Rewrite the instruction as one clear, "
                "literal edit: what to change, where in the picture, and what must stay exactly as it is (the "
                "people, faces, pose, clothes, background and framing). Don't describe the whole picture, and add "
                "nothing the user didn't ask for. Keep references such as \"Picture 2\" as they are.")
    else:
        what = ("a short-film planner. Expand the idea into one vivid paragraph: setting, who is in it, what "
                "happens, mood, visual style and sound. Don't write a script or scene list.")
    rules = [
        f"You improve prompts for {what}",
        "Keep the user's subject and intent; never swap in a different idea.",
        "Text in quotation marks is exact text that must appear (a sign, a poster, a title, a spoken line). Copy it "
        "character for character inside the same quotation marks. Never translate, transliterate, correct or "
        "rephrase quoted text, whatever its language or script (Urdu, Arabic, Hindi...).",
        "Apart from quoted text, write in English.",
        f"One paragraph, at most {cap} words, no preamble, no negative prompt, no names of real people.",
    ]
    if model and model.startswith("qwen_image"):
        rules.append("This model draws lettering well: say where any quoted text sits and its lettering style.")
    if style:
        rules.append(f"The '{style}' style is applied separately; don't contradict it.")
    if brand_context:
        rules.append("Keep the brand's colours and look; don't invent slogans or extra text.\n" + brand_context)
    rules.append('Reply as JSON: {"prompt": the improved prompt, "notes": a few words on what you added}.')
    return [{"role": "system", "content": "\n".join(rules)},
            {"role": "user", "content": f"Prompt:\n{prompt.strip()}"}]


def _accept(original: str, kind: str, data: Enhanced) -> dict:
    text = " ".join((data.prompt or "").split())
    if not text:
        return _result(original, notes=UNAVAILABLE)
    missing = [q for q in quoted(original) if q not in text]
    if missing:
        # the one promise we make; better the user's own words than a mangled sign
        log.warning("enhancer dropped or changed quoted text %r; keeping the original", missing[:2])
        return _result(original, notes="kept your prompt: the enhancer changed quoted text")
    if len(text.split()) > WORD_CAP[kind] * 1.5:
        return _result(original, notes="kept your prompt: the enhancer ran long")
    return _result(original, text, (data.notes or "").strip()[:200] or None)


def _plan(prompt: str, kind: str, mode: str, model, style, brand_context) -> tuple[dict | None, tuple]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    key = (prompt.strip(), kind, model or "", style or "", brand_context or "")
    if mode == "auto" and not needs_enhance(prompt):
        return _result(prompt), key
    return _cache_get(key), key


async def enhance(prompt: str, kind: str = "image", *, mode: str = "auto", model: str | None = None,
                  style: str | None = None, brand_context: str = "") -> dict:
    """{enhanced, changed, notes?}. Never raises for LLM trouble: the original comes back instead."""
    early, key = _plan(prompt, kind, mode, model, style, brand_context)
    if early is not None:
        return early
    timeout = get_settings().enhance_timeout_s
    try:
        res = await asyncio.wait_for(
            chat("creative", messages(prompt, kind, model=model, style=style, brand_context=brand_context),
                 schema=Enhanced, think=False, temperature=0.6, max_tokens=700, timeout=timeout),
            timeout + 5)
    except (LLMError, asyncio.TimeoutError) as e:
        log.warning("prompt enhancer unavailable: %s", e)
        return _result(prompt, notes=UNAVAILABLE)
    out = _accept(prompt, kind, res.data)
    if out["changed"]:
        _cache_put(key, out)
    return out


def enhance_sync(prompt: str, kind: str = "image", *, mode: str = "auto", model: str | None = None,
                 style: str | None = None, brand_context: str = "", tick=None, llm: dict | None = None) -> dict:
    """Worker side. `tick` can raise JobCancelled, which is allowed through."""
    early, key = _plan(prompt, kind, mode, model, style, brand_context)
    if early is not None:
        return early
    try:
        # `llm`: chat keywords from the caller, e.g. a film's FILM_LLM_MODEL (llm.film.overrides)
        res = chat_sync("creative", messages(prompt, kind, model=model, style=style, brand_context=brand_context),
                        tick=tick, **{"schema": Enhanced, "think": False, "temperature": 0.6, "max_tokens": 700,
                                      "timeout": get_settings().enhance_timeout_s, **(llm or {})})
    except LLMError as e:
        log.warning("prompt enhancer unavailable: %s", e)
        return _result(prompt, notes=UNAVAILABLE)
    out = _accept(prompt, kind, res.data)
    if out["changed"]:
        _cache_put(key, out)
    return out


# ---------------------------------------------------------------- generators

def marker(mode: str | None, prompt_enhanced: bool, kind: str, *, style: str | None = None,
           brand_kit_id: str | None = None) -> dict:
    """What a generator stores in params.magic_prompt; the job acts on status "pending"."""
    mode = mode or get_settings().magic_prompt_default
    if mode == "off":
        return {"mode": "off", "status": "off"}
    if prompt_enhanced:
        return {"mode": mode, "status": "client"}
    out = {"mode": mode, "kind": kind, "status": "pending"}
    if style:
        out["style"] = style
    if brand_kit_id:
        out["brand_kit_id"] = brand_kit_id
    return out


def splice(full: str, original: str, enhanced: str) -> str | None:
    """Swap the user's words at the head of the stored prompt (style/brand suffixes follow them)."""
    base = original.strip()
    # an edit's stored prompt opens with its "Picture 1..3" reference lines; the user's words follow them
    at = 0 if full.startswith(base) else full.find(base)
    if at < 0 or not base:
        return None
    rest = full[at + len(base):].lstrip(" .")
    head = enhanced.strip().rstrip(" .")
    return full[:at] + (f"{head}. {rest}" if rest else head)


def brand_context_for(db, workspace_id: str, kit_id: str | None) -> str:
    if not kit_id:
        return ""
    from app import brand

    kit = brand.get_kit(db, workspace_id, kit_id)
    return brand.prompt_context(kit) if kit is not None else ""


def run_in_job(ctx, gen) -> None:
    """Called by the worker before the GPU step. Commits the new prompt so a retry doesn't redo it."""
    params = dict(gen.params or {})
    mp = params.get("magic_prompt") or {}
    if mp.get("status") != "pending":
        return
    original = (params.get("user_prompt") or gen.prompt or "").strip()
    ctx.progress(0.01, "Improving the prompt")
    out = enhance_sync(original, mp.get("kind") or "image", mode=mp.get("mode") or "auto",
                       model=params.get("model"), style=mp.get("style"),
                       brand_context=brand_context_for(ctx.db, gen.workspace_id, mp.get("brand_kit_id")),
                       tick=lambda: ctx.progress(0.01, "Improving the prompt"))
    changed = out["changed"]
    if changed:
        new = splice(gen.prompt or "", original, out["enhanced"])
        if new is None:
            log.warning("generation %s: stored prompt doesn't start with the user's words; not enhancing", gen.id)
            changed = False
        else:
            gen.prompt = new
    params.update(original_prompt=original, enhanced_prompt=out["enhanced"] if changed else original,
                  magic_prompt={**mp, "status": "done", "changed": changed,
                                **({"notes": out["notes"]} if out.get("notes") else {})})
    gen.params = params
    ctx.db.commit()
