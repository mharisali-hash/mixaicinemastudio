"""Songs sung in a chosen voice (owner, 7 Nov): a boy, a girl, a children's choir, or any Library voice.

The song models only take "male / female vocals" as style words, so the song is made with the nearest stock
vocal first. Then the vocals are split off (the Dub separation model), sung again in the target voice by
Seed-VC (singing voice conversion, its own venv) and laid back over the instrumental.

The child voices are designed voices, never a recording of a real child. A choir is three of them a few
milliseconds apart, spread across the stereo field.
"""
import hashlib
import json
import logging
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import venv_runner, voices
from app.config import get_settings

log = logging.getLogger("mixai.song_voice")

RUNNER = "sing_convert"
VOCALS = ("boy", "girl", "choir", "voice")
LABELS = {"boy": "a boy", "girl": "a girl", "choir": "a children's choir"}

# fixed seeds, so "boy" is the same boy in every song
CHILD = {
    "boy": ("A young boy, about eight years old, with a bright, clear and slightly breathy child's voice", 4127),
    "girl": ("A young girl, about eight years old, with a light, sweet and clear child's voice", 9033),
    "girl2": ("A young girl, about ten years old, with a soft, warm and gentle child's voice", 2261),
}
# voice, delay (s), stereo balance (-1 left .. 1 right)
CHOIR = (("boy", 0.0, -0.45), ("girl", 0.022, 0.45), ("girl2", 0.037, 0.0))

# about ten seconds of plain speech: only its timbre matters, so English is fine for every song language
REF_TEXT = ("The morning light came softly through the window, and everyone hurried out into the garden "
            "to see how many new flowers had opened overnight.")


def enabled() -> bool:
    return get_settings().song_voice_engine == "seed-vc"


def _designed(key: str) -> dict:
    description, seed = CHILD[key]
    return {"kind": "design", "name": key, "design": {"description": description, "seed": seed,
                                                      "engine": voices.default_engine()}}


def plan(db: Session, workspace_id: str, vocal: str, voice_id: str | None, lyrics: str) -> dict:
    """Checked at queue time; the result rides in the song's params as `sing_as`."""
    if not enabled():
        raise HTTPException(422, "Singing in another voice isn't set up on this server yet")
    if not lyrics.strip():
        raise HTTPException(422, "Add lyrics: a voice needs words to sing")
    if vocal == "voice":
        v = voices.find_voice(db, workspace_id, voice_id)
        if v is None:
            raise HTTPException(422, "Pick the voice to sing in" if not voice_id
                                else "That voice isn't in the library any more")
        base = "male" if (v.gender_hint or "").lower() == "male" else "female"
        return {"vocal": vocal, "base": base, "name": v.name, "voice_id": v.id,
                "targets": [{"voice": voices.voice_spec(db, v), "delay": 0.0, "pan": 0.0}]}
    # children sing nearer a woman's range than a man's, so the stock female vocal is the better start
    if vocal == "choir":
        targets = [{"voice": _designed(k), "delay": d, "pan": p} for k, d, p in CHOIR]
    else:
        targets = [{"voice": _designed(vocal), "delay": 0.0, "pan": 0.0}]
    return {"vocal": vocal, "base": "female", "name": LABELS[vocal], "targets": targets}


def _ref_key(spec: dict) -> str:
    return hashlib.sha1(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def reference(spec: dict, driver, progress) -> Path:
    """A clip of the target voice for Seed-VC to copy: a cloned voice's own clip, otherwise ten seconds
    spoken by it once and kept."""
    s = get_settings()
    if spec.get("ref_file"):
        path = s.data_dir / spec["ref_file"]
        if not path.is_file():
            raise RuntimeError(f"The reference clip of “{spec.get('name') or 'that voice'}” is missing")
        return path
    cache = s.data_dir / "song_voices" / f"{_ref_key(spec)}.flac"
    if cache.is_file():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    engine = (spec.get("design") or {}).get("engine") or voices.default_engine()
    params = {"kind": "speech", "engine": engine, "model": engine, "language": "en", "text": REF_TEXT,
              "text_spoken": REF_TEXT, "voice": spec, "duration_s": None}
    seed = (spec.get("design") or {}).get("seed")
    part = cache.with_name(f".{cache.stem}.part.flac")
    try:
        driver.generate_audio(REF_TEXT, params, seed, part, progress)
        part.replace(cache)
    finally:
        part.unlink(missing_ok=True)
    return cache


def convert(src: Path, ref: Path, out: Path, progress, lane: str | None = None, run=None) -> Path:
    s = get_settings()
    job = {"source": str(src), "target": str(ref), "out": str(out), "repo": s.song_voice_repo,
           "steps": s.song_voice_steps}
    (run or venv_runner.run)(RUNNER, s.song_voice_venv, job, lane=lane, need_gb=s.song_voice_vram_gb,
                             progress=progress)
    if not out.is_file():
        raise RuntimeError("The voice change wrote nothing")
    return out


def mix_graph(targets: list[dict]) -> str:
    # input 0 is the instrumental; every sung part is placed, panned and quietened so a choir isn't louder
    level = 1.0 if len(targets) == 1 else round(1.6 / len(targets), 3)
    parts, labels = [], ["[0:a]"]
    for n, t in enumerate(targets, start=1):
        ms = int(round(float(t.get("delay") or 0) * 1000))
        chain = "aresample=48000,aformat=channel_layouts=stereo"
        if ms:
            chain += f",adelay={ms}|{ms}"
        if t.get("pan"):
            chain += f",stereotools=balance_out={float(t['pan']):.2f}"
        parts.append(f"[{n}:a]{chain},volume={level}[v{n}]")
        labels.append(f"[v{n}]")
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
                 "loudnorm=I=-14:TP=-1:LRA=11,aresample=48000[out]")
    return ";".join(parts)


def remix(instrumental: Path, sung: list[tuple[Path, dict]], dst: Path) -> None:
    from app.dub import engines

    args = ["-i", str(instrumental)]
    for path, _ in sung:
        args += ["-i", str(path)]
    args += ["-filter_complex", mix_graph([t for _, t in sung]), "-map", "[out]", "-c:a", "flac", str(dst)]
    engines._ffmpeg(args)


def apply(ctx, gen, path: Path, driver) -> None:
    """Worker step after the song model: the same file, now sung in the chosen voice."""
    p = gen.params or {}
    sing = p.get("sing_as") or {}
    targets = sing.get("targets") or []
    if not targets:
        return
    s = get_settings()
    work = path.parent / f".{path.stem}.sing"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    name = sing.get("name") or "the chosen voice"
    lane = getattr(driver, "lane", None)
    try:
        ctx.progress(0.8, "Separating the vocals")
        res = driver.separate(path, work / "split", s.dub_separation_model,
                              lambda f, m: ctx.progress(0.8 + 0.05 * f, "Separating the vocals"))
        vocals, inst = Path(res["vocals"]), Path(res["instrumental"])
        sung = []
        share = 0.12 / len(targets)
        for n, t in enumerate(targets):
            at = 0.85 + share * n
            ref = reference(t["voice"], driver, lambda f, m: ctx.progress(at, f"Preparing the voice of {name}"))
            out = work / f"sung_{n}.wav"
            convert_fn = getattr(driver, "convert_singing", None)
            tick = lambda f, m, at=at: ctx.progress(at + share * f, f"Singing it as {name}")  # noqa: E731
            if convert_fn is not None:
                convert_fn(vocals, ref, out, tick)
            else:
                convert(vocals, ref, out, tick, lane=lane)
            sung.append((out, t))
        ctx.progress(0.98, "Mixing the song")
        mixed = work / "mixed.flac"
        remix(inst, sung, mixed)
        mixed.replace(path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    gen.params = {**p, "sung_as": name}
