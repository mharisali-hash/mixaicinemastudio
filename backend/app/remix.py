"""Remix a song (M24, contract v43): effect versions, a new beat, a new voice, mashups and medleys.

Every version is a Library audio item of its own, made by one job, so a version that fails doesn't take the
others with it. Effect versions and medleys are plain ffmpeg on the general lane (`audio_remix_fx`); anything
that needs the vocals split off, a new instrumental from ACE-Step or Seed-VC runs on an audio GPU
(`audio_remix`). The split is cached per source version, so four versions of one song separate it once.
Nothing here is a new model: the separator, ACE-Step 1.5 and Seed-VC are the ones Stems and Song already use.
"""
import logging
import random
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app import audio_analysis as aa
from app import audio_edit as ae
from app import audio_jobs as aj
from app import library as lib
from app import models_catalog as mc
from app import remix_analysis as ra
from app import rights, song_voice
from app.config import get_settings
from app.models import Generation, Job, MediaItem, new_id
from app.services import generation_file, job_out, new_seed

log = logging.getLogger("mixai.remix")

JOB = "audio_remix"
FX_JOB = "audio_remix_fx"
TAG = "remix"
SR = 48000
MAX_SOURCES = 3
MAX_SOURCE_S = 10 * 60
STEMS_KEEP_S = 7 * 24 * 3600
MEDLEY_PART_S = 40.0
# slowed + reverb: creators mostly sit at 0.8x; the reverb tail is about three seconds
SLOWED = 0.8
T60 = 3.0
ACTIVE = ("queued", "running")
TAIL = "loudnorm=I=-14:TP=-1:LRA=11,aresample=48000"
HEAD = "aresample=48000,aformat=channel_layouts=stereo"


@dataclass(frozen=True)
class Recipe:
    id: str
    label: str
    hint: str
    gpu: bool = False
    min_sources: int = 1
    max_sources: int = 1


RECIPES = {r.id: r for r in (
    Recipe("slowed_reverb", "Slowed + reverb", "Slower and lower, in a big room"),
    Recipe("sped_up", "Sped up", "Faster and brighter"),
    Recipe("nightcore", "Nightcore", "Much faster, higher, sparkly"),
    Recipe("lofi", "Lo-fi", "Warm, dusty and a little slower"),
    Recipe("bass_boost", "Bass boost", "Heavier low end"),
    Recipe("eight_d", "8D audio", "Circles around your head; use headphones"),
    Recipe("key_tempo", "Key and tempo", "Your own key and speed"),
    Recipe("new_beat", "New beat", "The vocals over freshly made music", gpu=True),
    Recipe("new_voice", "New voice", "The song sung by another voice", gpu=True),
    Recipe("mashup", "Mashup", "The vocals of one song over the music of another", gpu=True, min_sources=2,
           max_sources=MAX_SOURCES),
    Recipe("medley", "Medley", "The best part of each song, one after another", min_sources=2,
           max_sources=MAX_SOURCES),
)}
FX = ("slowed_reverb", "sped_up", "nightcore", "lofi", "bass_boost", "eight_d", "key_tempo")

# what Auto picks a new beat from; plain style words ACE-Step knows well
STYLES = ("lo-fi hip hop, mellow keys, vinyl drums", "deep house, warm bass, four on the floor",
          "afrobeats, bright percussion, guitar licks", "acoustic guitar and soft piano, unplugged",
          "trap, 808 bass, crisp hi-hats", "cinematic orchestral strings, big drums",
          "qawwali fusion, tabla and harmonium, modern drums", "synthwave, retro analog synths, gated drums",
          "reggaeton, dembow rhythm, plucked synths", "jazz trio, upright bass, brushed drums")


class RemixError(RuntimeError):
    pass


def source_tag(item_id: str) -> str:
    return f"{TAG}:{item_id.replace('-', '')}"


# ---------------------------------------------------------------- the effect graphs

def _rate(r: float) -> list[str]:
    # resampling moves pitch and tempo together, the way the slowed / nightcore sound is made
    return [f"asetrate={round(SR * r)}", f"aresample={SR}"]


def pitch_tempo(semitones: float, tempo: float, rubberband: bool) -> list[str]:
    ratio = 2 ** (semitones / 12)
    if not semitones:
        return ae.atempo(tempo)
    if rubberband:
        # quality pitch and both channels together: the default mode smears the stereo image of a full mix
        return [f"rubberband=tempo={tempo:.6f}:pitch={ratio:.6f}:pitchq=quality:channels=together"]
    return [*_rate(ratio), *ae.atempo(tempo / ratio)]


def fx_chain(recipe: str, options: dict | None = None, rubberband: bool = False) -> list[str]:
    o = options or {}
    if recipe == "slowed_reverb":
        # the reverb is a convolution (fx_graph) when an impulse response is given; this is the fallback
        return [*_rate(SLOWED), "aecho=0.8:0.7:60|97|143|211:0.35|0.28|0.2|0.14", "lowpass=f=9000"]
    if recipe == "sped_up":
        return _rate(1.15)
    if recipe == "nightcore":
        return [*_rate(1.25), "crystalizer=i=1.5"]
    if recipe == "lofi":
        # slow wow and a quicker flutter, the way a worn tape wobbles
        return [*_rate(0.95), "vibrato=f=0.5:d=0.04", "vibrato=f=6:d=0.015", "highpass=f=120", "lowpass=f=4500",
                "acrusher=bits=11:mode=log:aa=1:mix=0.25",
                "acompressor=threshold=-20dB:ratio=3:attack=20:release=250:makeup=2"]
    if recipe == "bass_boost":
        return ["bass=g=8:f=90", "equalizer=f=60:t=q:w=1:g=3", ae.LIMIT]
    if recipe == "eight_d":
        return ["apulsator=hz=0.125:amount=0.85", "aecho=0.8:0.6:40|71:0.2|0.12"]
    if recipe == "key_tempo":
        return pitch_tempo(float(o.get("semitones") or 0), float(o.get("tempo") or 1.0), rubberband)
    raise RemixError(f"Unknown effect '{recipe}'")


def fx_graph(recipe: str, options: dict | None, duration: float, rubberband: bool = False,
             ir: bool = False) -> str:
    """Input 0 is the song; with ir=True input 1 is a reverb impulse response (slowed + reverb)."""
    if recipe == "slowed_reverb" and ir:
        dry = ",".join([HEAD, *_rate(SLOWED), "lowpass=f=9000"])
        return (f"[0:a]{dry}[d];[1:a]aformat=channel_layouts=stereo[ir];"
                f"[d][ir]afir=dry=6:wet=4,{TAIL}[out]")
    chain = ",".join([HEAD, *fx_chain(recipe, options, rubberband)])
    if recipe == "lofi":
        # a quiet brown-noise bed under it, the length of the slowed song
        noise = f"anoisesrc=d={duration / 0.95 + 0.5:.3f}:c=brown:r={SR}:a=0.015,aformat=channel_layouts=stereo"
        return (f"[0:a]{chain}[m];{noise}[n];[m][n]amix=inputs=2:duration=first:normalize=0,{TAIL}[out]")
    return f"[0:a]{chain},{TAIL}[out]"


def _ffmpeg(args: list[str], tick=None) -> None:
    ae.run(args, tick=tick, loglevel="error")


def reverb_ir() -> Path | None:
    """A hall-like impulse response, made once: decaying stereo noise, darker as it fades, left and right
    different so the tail is wide. Convolution sounds far smoother than echo taps."""
    if not ae.has_filter("afir"):
        return None
    path = get_settings().data_dir / "remix" / "hall_ir_v1.wav"
    if path.is_file():
        return path
    import numpy as np

    rng = np.random.default_rng(1729)
    n, pre = int(SR * T60), int(SR * 0.02)
    t = np.arange(n) / SR
    env = np.exp(-6.91 * t / T60)  # -60 dB at T60
    chans = []
    for _ in range(2):
        x = rng.standard_normal(n) * env
        # a one-pole low-pass that closes as the tail decays: the high end dies first in a real room
        a = 0.35 + 0.55 * (t / T60)
        y = np.empty_like(x)
        acc = 0.0
        for i in range(n):
            acc = a[i] * acc + (1 - a[i]) * x[i]
            y[i] = acc
        chans.append(np.concatenate([np.zeros(pre), y]))
    ir = np.stack(chans, axis=1)
    ir /= np.sqrt((ir ** 2).sum(axis=0)).max()
    pcm = (np.clip(ir * 0.5, -1, 1) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    import wave

    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)
    return path


def render_fx(recipe: str, options: dict | None, src: Path, dst: Path, tick=None) -> None:
    ir = reverb_ir() if recipe == "slowed_reverb" else None
    graph = fx_graph(recipe, options, aa.stream_info(src).duration, ae.has_rubberband(), ir=ir is not None)
    extra = ["-i", str(ir)] if ir else []
    _ffmpeg(["-i", str(src), *extra, "-vn", "-filter_complex", graph, "-map", "[out]", "-c:a", "flac", str(dst)],
            tick)


def medley_graph(parts: list[dict], xfade: float) -> str:
    """parts: start, length (source seconds) and the pitch/tempo moves of each song, in order."""
    nodes, labels = [], []
    for n, p in enumerate(parts):
        moves = pitch_tempo(p.get("semitones", 0), p.get("tempo", 1.0), p.get("rubberband", False))
        chain = [HEAD, f"atrim=start={p['start']:.3f}:duration={p['length']:.3f}", "asetpts=PTS-STARTPTS", *moves]
        nodes.append(f"[{n}:a]{','.join(chain)}[s{n}]")
        labels.append(f"s{n}")
    cur = labels[0]
    for n, nxt in enumerate(labels[1:], start=1):
        out = f"x{n}"
        # equal-power curves: a straight tri dips in the middle of the blend
        nodes.append(f"[{cur}][{nxt}]acrossfade=d={xfade:.3f}:c1=qsin:c2=qsin[{out}]")
        cur = out
    total = sum(p["length"] / p.get("tempo", 1.0) for p in parts) - xfade * (len(parts) - 1)
    nodes.append(f"[{cur}]afade=t=in:d=0.5,afade=t=out:st={max(0.0, total - 3):.3f}:d=3,{TAIL}[out]")
    return ";".join(nodes)


def over_graph(length: float, music_moves: list[str], delay_s: float, music_level: float = 0.8) -> str:
    """Vocals (input 0) over a moved, delayed, looped instrumental (input 1). The music makes room for the voice:
    a small presence dip, and a gentle duck keyed by the vocals, so words stay clear without pumping."""
    ms = int(round(delay_s * 1000))
    chain = [HEAD, *music_moves]
    if ms:
        chain.append(f"adelay={ms}|{ms}")
    chain += [f"atrim=duration={length + 0.5:.3f}", "equalizer=f=2500:t=q:w=1:g=-3", f"volume={music_level}"]
    return (f"[0:a]{HEAD},highpass=f=100,asplit=2[v][key];[1:a]{','.join(chain)}[m];"
            "[m][key]sidechaincompress=threshold=0.05:ratio=4:attack=5:release=150:detection=rms:mix=0.8[md];"
            f"[v][md]amix=inputs=2:duration=first:normalize=0,{TAIL}[out]")


def over(vocals: Path, music: Path, dst: Path, music_moves: list[str], delay_s: float, music_level: float = 0.8,
         tick=None) -> None:
    """Vocals over a (moved, delayed, looped) instrumental, as long as the vocals."""
    graph = over_graph(aa.stream_info(vocals).duration, music_moves, delay_s, music_level)
    _ffmpeg(["-i", str(vocals), "-stream_loop", "-1", "-i", str(music), "-filter_complex", graph,
             "-map", "[out]", "-c:a", "flac", str(dst)], tick)


# ---------------------------------------------------------------- what to make

def availability(db: Session, workspace_id: str) -> dict[str, str | None]:
    """recipe -> None when it can be made here, else a plain reason. Never mentions licences."""
    out: dict[str, str | None] = {k: None for k in RECIPES}
    if beat_model(db) is None:
        out["new_beat"] = "Making new music isn't available on your plan"
    if not song_voice.enabled():
        out["new_voice"] = "Singing in another voice isn't set up on this server yet"
    else:
        r = rights.for_session(db)
        if r is not None and not r.allows("song_voices"):
            out["new_voice"] = r.refusal("song_voices")
    return out


def beat_model(db: Session) -> mc.Model | None:
    r = rights.for_session(db)
    for m in mc.MODELS.values():
        if m.type == "audio" and m.template == "ace15_song" and not mc.hidden(m) and (r is None or r.model_ok(m.id)):
            return m
    return None


def auto_plan(n_sources: int, avail: dict[str, str | None], rng: random.Random | None = None) -> list[dict]:
    rng = rng or random.Random()
    if n_sources >= 2:
        plan = [{"recipe": "mashup", "options": {"vocals_from": 0, "music_from": 1}},
                {"recipe": "mashup", "options": {"vocals_from": 1, "music_from": 0}},
                {"recipe": "medley", "options": {}},
                {"recipe": "medley", "options": {"then": "slowed_reverb"}}]
        return plan
    last = ({"recipe": "new_beat", "options": {"style": rng.choice(STYLES)}} if avail.get("new_beat") is None
            else {"recipe": "eight_d", "options": {}})
    return [{"recipe": "slowed_reverb", "options": {}}, {"recipe": "sped_up", "options": {}},
            {"recipe": "lofi", "options": {}}, last]


def check_plan(plan: list[dict], n_sources: int, avail: dict[str, str | None]) -> None:
    if not plan:
        raise HTTPException(422, "Pick at least one kind of remix")
    for step in plan:
        r = RECIPES.get(step["recipe"])
        if r is None:
            raise HTTPException(422, f"Unknown remix '{step['recipe']}'")
        if avail.get(r.id):
            raise HTTPException(422, f"{r.label}: {avail[r.id]}")
        if n_sources < r.min_sources:
            raise HTTPException(422, f"{r.label} needs {r.min_sources} songs or more")
        if r.max_sources == 1 and n_sources > 1:
            raise HTTPException(422, f"{r.label} works on one song; remove the others or pick Mashup or Medley")
        o = step.get("options") or {}
        if r.id == "mashup":
            a, b = int(o.get("vocals_from", 0)), int(o.get("music_from", 1))
            if a == b or not (0 <= a < n_sources and 0 <= b < n_sources):
                raise HTTPException(422, "A mashup takes the vocals and the music from two different songs")
        if r.id == "new_voice" and o.get("vocal") not in ("boy", "girl", "choir", "voice"):
            raise HTTPException(422, "Pick who sings the new version")
        if o.get("then") and o["then"] not in FX:
            raise HTTPException(422, f"Unknown effect '{o['then']}'")


def sources(db: Session, workspace_id: str, ids: list[str]) -> list[tuple[MediaItem, Generation]]:
    if not ids or len(ids) > MAX_SOURCES:
        raise HTTPException(422, f"Pick 1 to {MAX_SOURCES} songs")
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "The same song is in there twice")
    out = []
    for i in ids:
        item = aj._item(db, workspace_id, i, "Song")
        if item.removed_at is not None:
            raise HTTPException(404, "Song not found")
        if item.kind not in ("audio", "video"):
            raise HTTPException(422, "Pick a song (audio) or a video with sound")
        g = aj._version(db, item, None)
        aj._usable(g, f"{item.kind}/", "an audio clip" if item.kind == "audio" else "a video")
        info = aa.stream_info(generation_file(g))
        if not info.has_audio:
            raise HTTPException(422, f"“{item.title or 'That video'}” has no sound to remix")
        if info.duration > MAX_SOURCE_S:
            raise HTTPException(422, f"“{item.title or 'That song'}” is longer than {MAX_SOURCE_S // 60} minutes")
        out.append((item, g))
    return out


def _title(srcs: list[tuple[MediaItem, Generation]], step: dict) -> str:
    names = [(i.title or "Untitled") for i, _ in srcs]
    r, o = RECIPES[step["recipe"]], step.get("options") or {}
    if r.id == "mashup":
        base = f"{names[int(o.get('vocals_from', 0))]} × {names[int(o.get('music_from', 1))]}"
        label = "Mashup"
    elif r.id == "medley":
        base, label = " + ".join(names), "Medley"
    else:
        base, label = names[0], r.label
    if r.id == "new_beat" and o.get("style"):
        label = f"New beat ({o['style'].split(',')[0].strip()})"
    if r.id == "new_voice" and o.get("sing_name"):
        label = f"Sung by {o['sing_name']}"
    if o.get("then"):
        label += f", {RECIPES[o['then']].label.lower()}"
    return f"{base[:200]} — {label}"[:240]


def queue(db: Session, workspace_id: str, user_id: str | None, srcs: list[tuple[MediaItem, Generation]],
          plan: list[dict], folder_id: str | None) -> tuple[list[MediaItem], list[Job]]:
    items, jobs = [], []
    tags = lib.clean_tags([TAG, *(source_tag(i.id) for i, _ in srcs)])
    model = beat_model(db) if any(s["recipe"] == "new_beat" for s in plan) else None
    for step in plan:
        r, o = RECIPES[step["recipe"]], dict(step.get("options") or {})
        params = {"kind": "remix", "recipe": r.id, "options": o, "source_ids": [i.id for i, _ in srcs],
                  "source_generation_ids": [g.id for _, g in srcs],
                  "created_by": {"user_id": user_id, "flow": "remix"}}
        if r.id == "new_voice":
            sing = song_voice.targets_for(db, workspace_id, o["vocal"], o.get("voice_id"))
            o["sing_name"] = sing["name"]
            # sing_as is what the quota reads to ask for the Song voices right
            params.update(sing_as=sing, options=o)
        if r.id == "new_beat":
            o.setdefault("style", random.choice(STYLES))
            params.update(model=model.id, model_resolved=model.id, options=o)
        lead_item = srcs[0][0]
        item = MediaItem(id=new_id(), workspace_id=workspace_id, kind="audio", origin="generated",
                         title=_title(srcs, {"recipe": r.id, "options": o}), tags=list(tags),
                         folder_id=folder_id if folder_id is not None else lead_item.folder_id)
        db.add(item)
        db.flush()
        g = Generation(id=new_id(), workspace_id=workspace_id, project_id=None, target_type="media",
                       target_id=item.id, kind="audio", version=1, status="queued", prompt=item.title,
                       seed=new_seed(), params=params)
        db.add(g)
        db.flush()
        job = Job(workspace_id=workspace_id, type=JOB if r.gpu else FX_JOB, generation_id=g.id,
                  message=f"Waiting for a worker · {r.label}",
                  payload={"generation_id": g.id, "sources": [g2.id for _, g2 in srcs], "recipe": r.id,
                           "source_ids": [i.id for i, _ in srcs]})
        db.add(job)
        db.flush()
        g.job_id = job.id
        item.generation_id = g.id
        lib.touch(item)
        items.append(item)
        jobs.append(job)
    return items, jobs


# ---------------------------------------------------------------- the jobs

def _workdir(job: Job) -> Path:
    root = get_settings().data_dir / "workspaces" / job.workspace_id / "remix" / "work"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{job.id}-", dir=root))


def _wav(src: Path, dst: Path) -> Path:
    # one decode for audio and video alike
    _ffmpeg(["-i", str(src), "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)])
    return dst


def stems_for(ctx, driver, g: Generation, src: Path, at: float, share: float) -> tuple[Path, Path]:
    """Vocals and instrumental of one source version, split once and kept a week."""
    s = get_settings()
    root = s.data_dir / "workspaces" / g.workspace_id / "remix" / "stems"
    done = root / g.id
    vocals, inst = done / "vocals.flac", done / "instrumental.flac"
    if vocals.is_file() and inst.is_file():
        return vocals, inst
    root.mkdir(parents=True, exist_ok=True)
    _prune(root)
    tmp = Path(tempfile.mkdtemp(prefix=f".{g.id}-", dir=root))
    try:
        ctx.progress(at, "Separating the vocals")
        wav = _wav(src, tmp / "source.wav")
        res = driver.separate(wav, tmp / "out", s.dub_separation_model,
                              lambda f, m: ctx.progress(at + share * f, "Separating the vocals"))
        _ffmpeg(["-i", str(res["vocals"]), "-ar", str(SR), "-c:a", "flac", str(tmp / "vocals.flac")])
        _ffmpeg(["-i", str(res["instrumental"]), "-ar", str(SR), "-c:a", "flac", str(tmp / "instrumental.flac")])
        done.mkdir(exist_ok=True)
        (tmp / "vocals.flac").replace(vocals)
        (tmp / "instrumental.flac").replace(inst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return vocals, inst


def _prune(root: Path) -> None:
    # TODO: prune on a timer too; a workspace that stops remixing keeps its last week of splits
    cutoff = time.time() - STEMS_KEEP_S
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _sources(db: Session, job: Job) -> list[Path]:
    out = []
    for gid in (job.payload or {}).get("sources") or []:
        g = db.get(Generation, gid)
        path = generation_file(g) if g is not None else None
        if path is None or not path.is_file():
            raise RemixError("One of the songs to remix is gone")
        out.append(path)
    if not out:
        raise RemixError("There's no song to remix")
    return out


def _finish(ctx, gen: Generation, made: Path, then: str | None, work: Path) -> dict:
    db = ctx.db
    if then:
        ctx.progress(0.92, f"Adding {RECIPES[then].label.lower()}")
        after = work / "then.flac"
        render_fx(then, None, made, after)
        made = after
    folder, rel_folder = lib.media_dir(gen.workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{gen.id}.flac"
    ctx.progress(0.96, "Saving")
    shutil.move(str(made), dst)
    db.refresh(gen)
    duration = round(aa.stream_info(dst).duration, 3)
    rel = f"{rel_folder}/{dst.name}"
    gen.params = {**(gen.params or {}), "duration_s": duration}
    gen.file_path, gen.media_type, gen.status = rel, "audio/flac", "ready"
    return {"file_path": rel, "media_type": "audio/flac", "duration_s": duration, "bytes": dst.stat().st_size}


def _start(ctx) -> Generation:
    gen = aj._start(ctx)
    if (gen.params or {}).get("kind") != "remix":
        raise RemixError("This isn't a remix")
    return gen


def handle_fx(ctx) -> dict:
    """Effect versions and medleys: ffmpeg only."""
    gen = _start(ctx)
    p = gen.params or {}
    recipe, o = p.get("recipe"), p.get("options") or {}
    srcs = _sources(ctx.db, ctx.job)
    work = _workdir(ctx.job)
    t0 = time.monotonic()
    try:
        out = work / "out.flac"
        if recipe == "medley":
            ctx.progress(0.1, "Finding the best part of each song")
            medley(srcs, out, work)
        elif recipe in FX:
            ctx.progress(0.1, f"Making the {RECIPES[recipe].label.lower()} version")
            render_fx(recipe, o, srcs[0], out, tick=lambda: ctx.progress(0.5, RECIPES[recipe].label))
        else:
            raise RemixError(f"{recipe} isn't an effect")
        res = _finish(ctx, gen, out, o.get("then"), work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**res, "recipe": recipe, "seconds": round(time.monotonic() - t0, 3)}


def medley(srcs: list[Path], dst: Path, work: Path) -> None:
    rb = ae.has_rubberband()
    infos = [ra.analyse(s) for s in srcs]
    lead_bpm, lead_key = infos[0]["bpm"], infos[0]["key"]
    parts = []
    for n, (src, info) in enumerate(zip(srcs, infos)):
        length = min(MEDLEY_PART_S, info["duration_s"])
        start = ra.loudest_window(aa._decode_mono(src, ra.RATE), length)
        tempo = 1.0 if n == 0 else ra.tempo_ratio(lead_bpm, info["bpm"])
        semis = 0 if n == 0 else ra.semitones_between(lead_key, info["key"], harmonic=True)
        # a big key jump sounds worse shifted than left alone between songs
        parts.append({"start": start, "length": length, "tempo": tempo,
                      "semitones": semis if abs(semis) <= 3 else 0, "rubberband": rb})
    beats = 60.0 / lead_bpm if lead_bpm else 0.5
    shortest = min(p["length"] / p["tempo"] for p in parts)
    xfade = max(2.0, min(8 * beats, 8.0, shortest / 3))
    args = []
    for s in srcs:
        args += ["-i", str(s)]
    _ffmpeg([*args, "-vn", "-filter_complex", medley_graph(parts, xfade), "-map", "[out]", "-c:a", "flac", str(dst)])


def handle(ctx) -> dict:
    """New beat, new voice and mashups: a split first, then a GPU model or a second song."""
    gen = _start(ctx)
    p = gen.params or {}
    recipe, o = p.get("recipe"), p.get("options") or {}
    srcs = _sources(ctx.db, ctx.job)
    gens = [ctx.db.get(Generation, gid) for gid in (ctx.job.payload or {}).get("sources") or []]
    driver = ctx.driver_factory()
    work = _workdir(ctx.job)
    t0 = time.monotonic()
    extra: dict = {}
    try:
        out = work / "out.flac"
        if recipe == "new_beat":
            extra = new_beat(ctx, driver, p.get("model"), gens[0], srcs[0], o, out, work)
        elif recipe == "new_voice":
            new_voice(ctx, driver, gens[0], srcs[0], p.get("sing_as") or {}, out, work)
        elif recipe == "mashup":
            a, b = int(o.get("vocals_from", 0)), int(o.get("music_from", 1))
            extra = mashup(ctx, driver, (gens[a], srcs[a]), (gens[b], srcs[b]), out, work)
        else:
            raise RemixError(f"Unknown remix '{recipe}'")
        res = _finish(ctx, gen, out, o.get("then"), work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**res, **extra, "recipe": recipe, "seconds": round(time.monotonic() - t0, 3)}


def _bar(bpm: float | None) -> float:
    return 4 * 60.0 / bpm if bpm else 2.0


def new_beat(ctx, driver, model_id: str | None, g: Generation, src: Path, o: dict, dst: Path, work: Path) -> dict:
    info = ra.analyse(src)
    vocals, inst = stems_for(ctx, driver, g, src, 0.05, 0.3)
    model = mc.MODELS.get(model_id or "")
    if model is None:
        raise RemixError("The music model for a new beat isn't available")
    seconds = min(info["duration_s"], model.max_duration_s or info["duration_s"])
    style = o.get("style") or random.choice(STYLES)
    params = {"kind": "song", "model": model.id, "lyrics": "", "vocal": "none", "duration_s": round(seconds, 1)}
    if info["bpm"]:
        params["bpm"] = int(round(info["bpm"]))
    if info["key"]:
        params["keyscale"] = info["key"]
    ctx.progress(0.4, "Making the new music")
    beat = work / "beat.flac"
    driver.generate_audio(style, params, new_seed(), beat, lambda f, m: ctx.progress(0.4 + 0.4 * f,
                                                                                         "Making the new music"))
    if not beat.is_file():
        raise RemixError("The music model wrote nothing")
    # the model lands near the asked tempo, not on it; pull it the rest of the way, then line its beats up
    got = ra.analyse(beat)
    tempo = ra.tempo_ratio(info["bpm"], got["bpm"], 0.9, 1.1)
    moves = ae.atempo(tempo)
    moved = beat
    if moves:
        moved = work / "beat_moved.wav"
        _ffmpeg(["-i", str(beat), "-af", ",".join(moves), str(moved)])
    lag = ra.best_offset(aa._decode_mono(inst, ra.RATE), aa._decode_mono(moved, ra.RATE), _bar(info["bpm"]))
    ctx.progress(0.85, "Mixing the vocals over the new music")
    over(vocals, moved, dst, [], lag, music_level=0.85)
    return {"bpm": info["bpm"], "key": info["key"], "style": style, "beat_tempo": tempo, "offset_s": lag}


def new_voice(ctx, driver, g: Generation, src: Path, sing: dict, dst: Path, work: Path) -> None:
    targets = sing.get("targets") or []
    if not targets:
        raise RemixError("Nobody was picked to sing it")
    vocals, inst = stems_for(ctx, driver, g, src, 0.05, 0.35)
    name = sing.get("name") or "the new voice"
    lane = getattr(driver, "lane", None)
    share = 0.5 / len(targets)
    sung = []
    for n, t in enumerate(targets):
        at = 0.4 + share * n
        tick = (lambda f, m, at=at: ctx.progress(at + share * f, f"Singing it as {name}"))
        sung.append((song_voice._sing(driver, vocals, t, work / f"sung_{n}.wav", name, tick, lane), t))
    ctx.progress(0.9, "Mixing the song")
    song_voice.remix(inst, sung, dst)


def mashup(ctx, driver, lead: tuple[Generation, Path], music: tuple[Generation, Path], dst: Path,
           work: Path) -> dict:
    (ga, a), (gb, b) = lead, music
    ia, ib = ra.analyse(a), ra.analyse(b)
    vocals, inst_a = stems_for(ctx, driver, ga, a, 0.05, 0.3)
    _, inst_b = stems_for(ctx, driver, gb, b, 0.35, 0.3)
    tempo = ra.tempo_ratio(ia["bpm"], ib["bpm"])
    semis = ra.semitones_between(ia["key"], ib["key"], harmonic=True)
    moves = pitch_tempo(semis, tempo, ae.has_rubberband())
    ctx.progress(0.7, "Matching the tempo and key")
    moved = work / "music.wav"
    _ffmpeg(["-i", str(inst_b), "-af", ",".join([HEAD, *moves]) if moves else HEAD, str(moved)])
    lag = ra.best_offset(aa._decode_mono(inst_a, ra.RATE), aa._decode_mono(moved, ra.RATE), _bar(ia["bpm"]))
    ctx.progress(0.85, "Mixing the mashup")
    over(vocals, moved, dst, [], lag)
    return {"tempo": tempo, "semitones": semis, "offset_s": lag, "bpm": ia["bpm"], "key": ia["key"]}


# ---------------------------------------------------------------- listing

def active_jobs(db: Session, workspace_id: str, item_id: str) -> list[Job]:
    rows = db.scalars(select(Job).where(Job.workspace_id == workspace_id, Job.type.in_((JOB, FX_JOB)),
                                        Job.status.in_(ACTIVE)).order_by(Job.created_at.desc())).all()
    return [j for j in rows if item_id in ((j.payload or {}).get("source_ids") or [])]


def remixes_of(db: Session, workspace_id: str, item_id: str) -> dict:
    tag = source_tag(item_id)
    rows = db.scalars(select(MediaItem).where(MediaItem.workspace_id == workspace_id, MediaItem.kind == "audio",
                                              MediaItem.removed_at.is_(None),
                                              cast(MediaItem.tags, String).like(f'%"{tag}"%'))
                      .order_by(MediaItem.created_at.desc())).all()
    return {"source_id": item_id, "items": lib.items_out(db, list(rows)),
            "jobs": [job_out(j) for j in active_jobs(db, workspace_id, item_id)]}


def on_job_finished(db, job) -> None:
    """Keep the analysis next to a new remix, so its own tempo and key are ready if it's remixed again."""
    if job.type not in (JOB, FX_JOB) or job.status != "done" or not job.generation_id:
        return
    g = db.get(Generation, job.generation_id)
    path = generation_file(g) if g is not None else None
    if path is not None and path.is_file():
        try:
            ra.analyse(path)
        except Exception:
            log.warning("couldn't analyse remix %s", g.id, exc_info=True)
