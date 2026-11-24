"""Ideogram 4 captions: the model was trained on structured JSON captions, and its built-in safety filter
blocks plain-text prompts far more often (a harmless coffee poster came back "blocked" on 22 Oct, the same
idea as a caption rendered cleanly). The creative LLM writes the caption before the GPU step; the user's
prompt stays readable in the Library and the caption rides along in params.
"""
import asyncio
import json
import logging
import math
import re
import unicodedata

from pydantic import BaseModel, Field

from app.config import get_settings
from app.limits import PROMPT_MAX
from app.llm.client import LLMError, bare_tag, chat_sync, installed_models
from app.prompt_enhance import quoted

log = logging.getLogger("mixai.ideogram")

MODEL_ID = "ideogram4"
MAX_ELEMENTS = 8
# a faint grey name came back when the desc gave no colour
# every word here can end up printed: "as written" put a stray "as" on a book cover (22 Oct)
PLAIN_TEXT = "Clear, legible lettering in a colour that stands out strongly from the background."
MEDIUMS = ("photograph", "illustration", "3d_render", "painting", "graphic_design")
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
# clauses of the user's prompt that say how lettering should look; they go into every text element
_TYPE_WORDS = re.compile(r"typograph|font|letter|calligraph|serif|script|typeface|handwrit|\bcaps\b",
                         re.I)
_QUOTE_ANY = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|«([^»\n]+)»')
# what a lazy desc tends to say; the renderer prints such words, "Subject" turned up on a logo on 22 Oct
_LAZY = re.compile(r"\b(subject|text logo|the text|logo text|label)\b", re.I)

SYSTEM = """You turn an image idea into the JSON caption the Ideogram 4 renderer reads.

Return one JSON object with these keys:
- high_level_description: one sentence (under 50 words) naming the subject, the medium and the overall
  composition. Start with the subject itself, never "this image shows".
- style_description: aesthetics (a few keywords), lighting (one phrase), medium (photograph, illustration,
  3d_render, painting or graphic_design), color_palette (3-6 hex colours).
- background: the scene shell only - backdrop, walls, floor or ground, sky, weather, scene-wide light.
- elements: up to 8 things placed in the picture, front-most last. Each has type "obj" or "text",
  bbox [top, left, bottom, right] on a 0-1000 grid of the canvas, desc (one to three short sentences: what
  it is, its colours and materials, where it sits) and, for type "text", text: the exact characters to print.

Rules:
- Always include at least one "obj" element, even on a type-only poster (paper shapes, ornaments, a
  texture panel): a caption of nothing but text gets the picture blocked.
- One subject is one element (a person, a car, a cup with its saucer). Its parts go in its desc.
- Anything in background must not be repeated as an element. The floor or ground is always background.
- Every quoted phrase in the idea becomes a "text" element with exactly those characters, same spelling,
  same case, same accents. Never translate, correct or drop them, and add no other words to print.
- A text element's desc says how the letters look (typeface style, weight, colour, size) and where they
  sit. Never write words like "subject" or "text logo" in it: the renderer may print them.
- Text runs left to right, so a text bbox is wider than it is tall. Put long names on two lines with "\\n".
- high_level_description names every quoted phrase too.
- Plain, physical descriptions: colours, shapes, materials. No praise words, no camera jargon in descs.
- Keep the layout sensible for the canvas aspect ratio you are given.

Example. Aspect ratio: 2:3. Idea: bakery poster, a basket of fresh bread, "Daily Bread" in bold brown
rounded type, warm and rustic.
{"high_level_description": "A rustic bakery poster with a wicker basket of fresh loaves under the bold
headline 'Daily Bread'.", "style_description": {"aesthetics": "warm, rustic, simple", "lighting": "soft warm
daylight", "medium": "graphic_design", "color_palette": ["#F4E8D0", "#6B3E26", "#C8893F"]}, "background":
"A plain warm cream backdrop above a worn wooden table top.", "elements": [{"type": "text", "bbox": [70, 100,
230, 900], "text": "Daily Bread", "desc": "Bold rounded sans-serif headline in dark brown, large, centred
across the top."}, {"type": "obj", "bbox": [430, 150, 900, 850], "desc": "A wicker basket of crusty golden
loaves and baguettes on the table, centred in the lower half."}]}"""


class Style(BaseModel):
    aesthetics: str = ""
    lighting: str = ""
    medium: str = ""
    color_palette: list[str] = Field(default_factory=list)


class Element(BaseModel):
    type: str = "obj"
    bbox: list[float] | None = None
    desc: str = ""
    text: str | None = None


class Caption(BaseModel):
    high_level_description: str
    style_description: Style = Field(default_factory=Style)
    background: str = ""
    elements: list[Element] = Field(default_factory=list)


def is_caption(prompt: str) -> bool:
    """Already a JSON caption (pasted by hand or made earlier): used as it is."""
    text = (prompt or "").strip()
    if not text.startswith("{"):
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and "high_level_description" in data


def aspect(width: int, height: int) -> str:
    g = math.gcd(int(width), int(height)) or 1
    return f"{int(width) // g}:{int(height) // g}"


def _medium(raw: str) -> str:
    m = re.sub(r"[\s-]+", "_", (raw or "").strip().lower())
    return m if m in MEDIUMS else ""


def _clauses(prompt: str) -> list[tuple[str, list[str]]]:
    """(clause, the quoted phrases in it), split on commas outside quotes."""
    found: list[str] = []

    def hold(m):
        found.append(next(g for g in m.groups() if g is not None))
        return f"\x00{len(found) - 1}\x00"

    out = []
    for raw in re.split(r"[,;\n]", _QUOTE_ANY.sub(hold, prompt or "")):
        ids = [int(i) for i in re.findall(r"\x00(\d+)\x00", raw)]
        text = re.sub(r"\x00(\d+)\x00", lambda m: f'"{found[int(m.group(1))]}"', raw).strip()
        if text:
            out.append((text, [found[i] for i in ids]))
    return out


def _type_hint(prompt: str) -> str:
    # a clause that holds a quote says where that one line goes ("elegant calligraphy above arch"), not how
    # all the lettering looks; it travels with its own line instead (_placements)
    picked = [c for c, quotes in _clauses(prompt) if not quotes and _TYPE_WORDS.search(c)]
    return "; ".join(picked)[:200]


_SAYS = re.compile(r"\s*\b(reads|reading|says|saying|titled|that|which|with the words|of the words?|spelling)\s*$",
                   re.I)


def _placements(prompt: str) -> dict[str, str]:
    """Each quoted phrase -> plain location words from the brief ("ornate pillars left").

    Only lowercase prose with no quotes or markers: the renderer prints words it finds in a desc, and a
    "THIS LINE" placeholder ended up on a birth announcement (22 Oct)."""
    out = {}
    for clause, quotes in _clauses(prompt):
        rest, lead = clause, ""
        for i, q in enumerate(quotes):
            before, _, rest = rest.partition(f'"{q}"')
            words = before.strip()
            while _SAYS.search(words):
                words = _SAYS.sub("", words).strip()
            if i == 0:
                lead = " ".join(words.split()[:2])
            elif lead and not words.lower().startswith(lead.lower()):
                words = f"{lead} {words}".strip()
            if i == len(quotes) - 1 and rest.strip():
                # 'the words "STAY CURIOUS" in giant': the size came after the quote and was lost
                words = f"{words}, {rest.strip()}".strip(", ")
            if words and q not in out:
                out[q] = words[:160]
    return out


def _text_box(box: list[int] | None, text: str, ratio: str) -> list[int] | None:
    """A text box taller than its lines need gets the letters stood on end; better to let the model place it."""
    if not box:
        return None
    w, h = (int(v) for v in ratio.split(":"))
    lines = text.count("\n") + 1
    px_w, px_h = (box[3] - box[1]) * w, (box[2] - box[0]) * h
    return box if px_h <= px_w * 0.6 * lines else None


def _bbox(raw) -> list[int] | None:
    if not raw or len(raw) != 4:
        return None
    try:
        y1, x1, y2, x2 = (max(0, min(1000, int(round(float(v))))) for v in raw)
    except (TypeError, ValueError):
        return None
    if y2 <= y1 or x2 <= x1:
        return None
    return [y1, x1, y2, x2]


def assemble(cap: Caption, prompt: str, ratio: str) -> str:
    """The caption as the renderer wants it, with every quoted phrase of the prompt printed verbatim."""
    elements = []
    hint = _type_hint(prompt)
    where = _placements(prompt)
    for e in cap.elements[:MAX_ELEMENTS]:
        kind = "text" if e.type == "text" and (e.text or "").strip() else "obj"
        if not e.desc.strip() and kind == "obj":
            continue
        item: dict = {"type": kind}
        box = _bbox(e.bbox)
        desc = e.desc.strip()
        if kind == "text":
            box = _text_box(box, e.text.strip(), ratio)
            if len(desc.split()) < 4 or _LAZY.search(desc):
                desc = ""
            desc = _with_hint(desc or PLAIN_TEXT, hint)
        if box:
            item["bbox"] = box
        if kind == "text":
            item["text"] = e.text.strip()
        item["desc"] = desc
        elements.append(item)
    printed = {el.get("text") for el in elements}
    for words in quoted(prompt):
        # the LLM paraphrased or dropped a quoted line: the user's characters win
        if words not in printed:
            elements = [el for el in elements if not (el["type"] == "text" and _close(el["text"], words))]
            elements.append({"type": "text", "text": words,
                             "desc": _with_hint(PLAIN_TEXT, hint)})
    # an "obj" that is one of the quoted words drawn as a picture makes the word twice ("صبر", 22 Oct)
    lines = [w for w in quoted(prompt) if w.strip()]
    elements = [el for el in elements if el["type"] == "text" or not any(w in el["desc"] for w in lines)]
    for el in elements:
        if el["type"] == "obj":
            el["desc"] = _unquote(el["desc"], lines)
    if not any(el["type"] == "obj" for el in elements):
        # a caption of nothing but words came back "blocked by safety filter" twice for STAY CURIOUS; the
        # same poster with paper shapes as an object rendered (22 Oct)
        look = cap.style_description.aesthetics.strip().rstrip(".")
        elements.insert(0, {"type": "obj", "desc": "Decorative graphic shapes, texture and ornament that frame the "
                                                   f"lettering{', ' + look if look else ''}."[:300]})
    for el in elements:
        if el["type"] != "text":
            continue
        if el["text"] in where:
            el["desc"] = f"{el['desc'].rstrip('.')}. Placed: {where[el['text']]}."
        # "elegant calligraphy" turned "Alhamdulillah A Grandson Is Born" into made-up Arabic (22 Oct)
        el["desc"] = f"{el['desc'].rstrip('.')}. Written in {_script(el['text'])}."
        # a straight apostrophe: "Couldn’t" came out garbled three times running
        el["text"] = el["text"].replace("’", "'")
    _lockup(elements)
    _hero(elements)
    # the official schema (ideogram-oss prompting.md): fixed key order, uppercase hex, no aspect_ratio key
    # (the size comes from the latent), single quotes for text named in prose
    elements = [{k: el[k] for k in ("type", "bbox", "text", "desc") if k in el} for el in elements]
    s = cap.style_description
    style = {"aesthetics": s.aesthetics.strip(), "lighting": s.lighting.strip(), "medium": _medium(s.medium),
             "color_palette": [c.upper() for c in s.color_palette if _HEX.fullmatch(c.strip())][:16]}
    style = {k: v for k, v in style.items() if v}
    hld = _unquote(cap.high_level_description.strip(), lines)
    for q in lines:
        hld = hld.replace(f'"{q}"', f"'{q}'").replace(f"“{q}”", f"'{q}'")
    out: dict = {"high_level_description": hld}
    if style:
        out["style_description"] = style
    out["compositional_deconstruction"] = {
        "background": _unquote(cap.background.strip(), lines) or "A plain, simple backdrop.", "elements": elements}
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- exact text (app.ideogram_text)

EXACT_MIN_LINES = 3


def exact_lines(prompt: str) -> list[str]:
    """The quoted lines we set ourselves: three or more, all Latin. Fewer is a headline piece, where Ideogram's
    own lettering (neon, 3D, collage) is the point; Urdu and Arabic wait until English is right."""
    lines = [w for w in quoted(prompt) if w.strip()]
    if len(lines) < EXACT_MIN_LINES or not all(_script(w) == "Latin letters" for w in lines):
        return []
    return lines


_TEXTY = re.compile(r",?\s*\b(featuring|containing|bearing|with|displaying|showing|inscribed with)\s+(the\s+|some\s+)?"
                    r"(text|words?|lettering|inscriptions?|calligraphy|writing|names?|labels?)\b", re.I)
_WORDY = re.compile(r"(,\s*|\s+and\s+|\s+with\s+)?\b(\w+\s+)?(calligraphy|lettering|typography|inscriptions?|"
                    r"text|wording|writing)\b", re.I)
QUIET = ("The lower part of the same scene: a calm, softly lit, uncluttered area with smooth, even tone, "
         "continuing the setting without any objects.")


def blank_out(caption: str, prompt: str) -> tuple[str, list[dict]]:
    """The caption without its words, and the layout to set them into afterwards: [{text, bbox, hint, band}].

    The first try gave every line a blank plaque of its own where the small LLM had put it; the boxes were
    nonsense, the model drew white form-like panels, and "a pillar featuring text" got gibberish painted on
    it (22 Oct). Now the art fills the frame above a quiet band, and the lines are set there as one block."""
    data = json.loads(caption)
    els = data["compositional_deconstruction"]["elements"]
    texts = [el for el in els if el["type"] == "text"]
    objs = [el for el in els if el["type"] == "obj"]
    for o in objs:
        o["desc"] = _TEXTY.sub("", o["desc"]).strip()
    shares = [1.7] + [1.0] * (len(texts) - 1)
    unit = min(110, 400 / sum(shares))
    top = int(950 - unit * sum(shares))
    _fit_above([o for o in objs if "bbox" in o], top - 40)
    els[:] = objs + [{"type": "obj", "bbox": [top - 20, 0, 1000, 1000], "desc": QUIET}]
    hint, where = _type_hint(prompt), _placements(prompt)
    layout, y = [], top
    for el, share in zip(texts, shares):
        line, h = el["text"], int(unit * share)
        place = where.get(line) or where.get(line.replace("'", "’"), "")
        layout.append({"text": line, "bbox": [y, 90, y + h, 910], "hint": f"{el['desc']} {hint} {place}".strip(),
                       "band": top - 20})
        y += h
    # the lines named in the summary would be painted anyway
    hld = data["high_level_description"]
    for line in [ln["text"] for ln in layout]:
        hld = hld.replace(f"'{line}'", "").replace(f'"{line}"', "")
    # "celebratory calligraphy for a birth announcement" came back as made-up words in the arch (22 Oct)
    hld = _WORDY.sub("", _TEXTY.sub("", hld))
    data["high_level_description"] = re.sub(r"\s{2,}", " ", hld).strip()
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")), layout


def _lockup(elements: list[dict]) -> None:
    """Placed pictures but unplaced words: the renderer squeezed the name into the middle of the emblem at a
    few pixels high (Ouroboros, 22 Oct). Stack the words in a band at the bottom and fit the pictures above."""
    texts = [el for el in elements if el["type"] == "text"]
    placed = [el for el in elements if el["type"] == "obj" and "bbox" in el]
    if not texts or not placed or any("bbox" in t for t in texts):
        return
    heights = [(150 if i == 0 else 90) * (t["text"].count("\n") + 1) for i, t in enumerate(texts)]
    top = max(560, 1000 - 50 - sum(heights))
    scale = min(1.0, (1000 - 50 - top) / sum(heights))
    y = top
    for t, h in zip(texts, heights):
        h = int(h * scale)
        t["bbox"] = [y, 80, y + h, 920]
        y += h
    _fit_above(placed, top - 30)


def _fit_above(placed: list[dict], limit: int) -> None:
    """Pictures that reach below `limit` shrink into the space above it, keeping their shape."""
    for el in placed:
        y1, x1, y2, x2 = el["bbox"]
        if y2 <= limit:
            continue
        if y1 >= limit - 60:
            # wholly in the words' band (footprints on a threshold): let the model find it a place
            el.pop("bbox")
            continue
        old_h = max(1, y2 - y1)
        y1 = min(y1, 60)
        # shrink about the centre line so the emblem keeps its shape
        k = (limit - y1) / old_h
        cx, half = (x1 + x2) / 2, (x2 - x1) * k / 2
        el["bbox"] = [y1, int(cx - half), limit, int(cx + half)]


def _script(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if any(0x0600 <= ord(c) <= 0x06FF or 0x0750 <= ord(c) <= 0x08FF or 0xFB50 <= ord(c) <= 0xFEFF for c in letters):
        return "Arabic script"
    if all(ord(c) < 0x250 for c in letters):
        return "Latin letters"
    return "its own script"


def _hero(elements: list[dict]) -> None:
    """One line of words and nothing placed: it's the poster. Left to itself the renderer set "STAY CURIOUS"
    as a thin strip across the middle of an empty page (22 Oct)."""
    texts = [el for el in elements if el["type"] == "text"]
    if len(texts) != 1 or any("bbox" in el for el in elements):
        return
    texts[0]["bbox"] = [180, 60, 820, 940]


def _unquote(text: str, keep: list[str]) -> str:
    """Quoted words the user never asked for ('a jar label reading "BIO"') get printed: drop them."""
    def cut(m):
        inner = next(g for g in m.groups() if g is not None)
        return m.group(0) if inner in keep else ""
    return re.sub(r"\s{2,}", " ", _QUOTE_ANY.sub(cut, text)).strip()


def _with_hint(desc: str, hint: str) -> str:
    if not hint or hint.casefold() in desc.casefold():
        return desc
    return f"{desc.rstrip('.')}. Lettering: {hint}."


def _close(a: str, b: str) -> bool:
    a, b = a.casefold(), b.casefold()
    return a in b or b in a


def fallback(prompt: str, ratio: str) -> str:
    """No LLM: still a caption, which the filter takes far better than bare text."""
    cap = Caption(high_level_description=prompt.strip()[:PROMPT_MAX], background="")
    return assemble(cap, prompt, ratio)


def _caption_model() -> str | None:
    want = get_settings().ideogram_caption_model.strip()
    if not want:
        return None
    try:
        have = asyncio.run(installed_models())
    except LLMError:
        return want
    if bare_tag(want) in have:
        return want
    log.warning("IDEOGRAM_CAPTION_MODEL %s isn't installed; using the creative model", want)
    return None


def write(prompt: str, width: int, height: int, *, aspect_ratio: str | None = None,
          tick=None) -> tuple[str, bool]:
    """(caption, by_llm). aspect_ratio: the one the user picked; 832x1248 alone reduces to 2:3 but
    832x1280 to 13:20, which the model was never shown."""
    ratio = aspect_ratio if aspect_ratio and re.fullmatch(r"\d{1,2}:\d{1,2}", aspect_ratio) else aspect(width, height)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Aspect ratio: {ratio}\nIdea: {prompt.strip()}"}]
    try:
        model = _caption_model()
        # unload as soon as it answers: Ideogram's ~17 GB of weights need the same card a moment later
        kw = {"model": model, "keep_alive": "0s"} if model else {}
        res = chat_sync("creative", msgs, tick=tick, schema=Caption, think=False, temperature=0.4,
                        max_tokens=1400, timeout=180 if model else 120, **kw)
    except LLMError as e:
        log.warning("ideogram caption fell back to a plain wrapper: %s", e)
        return fallback(prompt, ratio), False
    return assemble(res.data, prompt, ratio), True


def run_in_job(ctx, gen) -> None:
    params = dict(gen.params or {})
    if params.get("model") != MODEL_ID or params.get("ideogram_caption") or is_caption(gen.prompt or ""):
        return
    ctx.progress(0.02, "Laying out the picture")
    # TODO: Print/Ad Studio know their exact lines and zones; pass those in instead of re-reading the prose
    caption, by_llm = write(gen.prompt or "", params.get("width") or 1024, params.get("height") or 1024,
                            aspect_ratio=params.get("aspect") or params.get("aspect_ratio"),
                            tick=lambda: ctx.progress(0.02, "Laying out the picture"))
    params.update(ideogram_caption=caption, ideogram_caption_by="llm" if by_llm else "fallback")
    if get_settings().ideogram_exact_text != "off" and exact_lines(gen.prompt or ""):
        caption, layout = blank_out(caption, gen.prompt or "")
        params.update(ideogram_caption=caption, exact_text=layout)
    gen.params = params
    ctx.db.commit()


# ---------------------------------------------------------------- second look at the lettering

class SeenText(BaseModel):
    lines: list[str] = Field(default_factory=list)
    # decorative made-up Arabic sat above an all-English announcement and wasn't listed as a line (22 Oct)
    other_script: bool = False


READ_TEXT = ("Transcribe every piece of text you can see in this picture exactly as it is written, letter for "
             "letter: headlines, small labels, stamps, signatures, watermarks. One entry per line of text. Do not "
             "fix spelling and do not add words that aren't there. No text at all: an empty list. "
             "Also say whether any writing in a non-Latin script appears anywhere, decorative calligraphy included "
             "(Arabic, Urdu, Chinese...). "
             'Reply as JSON {"lines": ["..."], "other_script": true or false}.')


def _tokens(text: str) -> list[str]:
    # the reader writes "Alḥamdulillāh" for a plain "Alhamdulillah": marks aren't mistakes
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", plain.casefold())


def expected_lines(caption: str) -> list[str]:
    try:
        els = json.loads(caption)["compositional_deconstruction"]["elements"]
    except (ValueError, KeyError, TypeError):
        return []
    return [e["text"] for e in els if e.get("type") == "text" and e.get("text")]


def problems(expected: list[str], seen: list[str]) -> dict:
    """Words in the picture nobody asked for, and asked-for words that didn't come out right."""
    want = set(_tokens(" ".join(expected)))
    got = _tokens(" ".join(seen))
    extra = sorted({t for t in got if t not in want and len(t) > 1})
    missing = sorted(want - set(got))
    # the same line painted twice (a chalkboard sign and again under it) is stray too
    asked = _tokens(" ".join(expected))
    repeats = sorted({t for t in set(got) if t in want and got.count(t) > asked.count(t)})
    # a dropped word costs double: a redraw that simply left the pillar names out beat one that garbled them
    return {"extra": extra[:12], "missing": missing[:12], "repeated": repeats[:12],
            "count": len(extra) + 2 * len(missing) + (1 if repeats else 0)}


def _readable(lines: list[str]) -> bool:
    # the vision model reads Latin type well and Arabic-script calligraphy badly; judging that would only
    # throw good pictures away
    return all(ord(c) < 0x250 for c in "".join(lines) if c.isalpha())


def read_text(path, tick=None) -> SeenText | None:
    from PIL import Image

    from app.ads.vision import b64

    with Image.open(path) as img:
        msgs = [{"role": "system", "content": "You read text in pictures. Answer only with JSON."},
                {"role": "user", "content": READ_TEXT, "images": [b64(img, 1280)]}]
    try:
        res = chat_sync("vision", msgs, schema=SeenText, temperature=0.0, max_tokens=500,
                        timeout=get_settings().vision_timeout_s, tick=tick)
    except LLMError as e:
        log.warning("lettering check skipped, vision model unavailable: %s", e)
        return None
    return SeenText(lines=[ln for ln in res.data.lines if ln.strip()], other_script=res.data.other_script)


def check(path, caption: str, tick=None, blank: bool = False) -> dict | None:
    """blank: the words are set afterwards, so any lettering the model painted on its own is stray."""
    expected = expected_lines(caption)
    if (not expected and not blank) or not _readable(expected):
        return None
    seen = read_text(path, tick)
    if seen is None:
        return None
    out = {**problems(expected, seen.lines), "seen": [s[:80] for s in seen.lines[:12]]}
    if seen.other_script:
        # only asked for Latin letters (check skips anything else), so any other script is stray
        out.update(count=out["count"] + 3, other_script=True)
    return out


def second_look(ctx, gen, render, abs_path, result):
    """After the picture: read its lettering back, and when there are stray or misspelt words, make it once
    more on another seed and keep whichever reads better. `render(seed, path)` runs the same job."""
    caption = (gen.params or {}).get("ideogram_caption")
    if not caption or not get_settings().ideogram_text_check:
        return result
    tick = lambda: ctx.progress(0.97, "Checking the lettering")  # noqa: E731
    tick()
    blank = bool((gen.params or {}).get("exact_text"))
    first = check(abs_path, caption, tick, blank=blank)
    record: dict = {"first": first}
    if first and first["count"]:
        retry_seed = (int(gen.seed or 0) + 7919) % 2147483647
        tmp = abs_path.with_name(f"{abs_path.stem}_retry{abs_path.suffix}")
        ctx.progress(0.5, "Redrawing: the lettering came out wrong")
        try:
            try:
                again = render(retry_seed, tmp)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "JobCancelled":
                    raise
                # the first picture is still a picture; a failed redraw mustn't cost the user it
                log.warning("lettering redraw failed, keeping the first picture: %s", e)
                record["kept"] = "first"
                gen.params = {**(gen.params or {}), "text_check": record}
                return result
            second = check(tmp, caption, tick, blank=blank)
            record["second"] = second
            if second is not None and second["count"] < first["count"]:
                tmp.replace(abs_path)
                gen.seed, result = retry_seed, again
                record["kept"] = "second"
            else:
                record["kept"] = "first"
        finally:
            tmp.unlink(missing_ok=True)
    gen.params = {**(gen.params or {}), "text_check": record}
    return result
