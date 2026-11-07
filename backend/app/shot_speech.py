"""Character voices in shots (contract v26): a shot's lines spoken in each character's voice, laid on one
speech track, and the take lip-synced to it.

prepare_shot_generation records what the take will say (`plan`). Each line is an ordinary speech generation
from voices.queue_speech on a Library item tagged shot:<id>, hidden like dialogue takes (library.HIDDEN_TAGS).
The take routes queue the lines straight away; anything else that queues a take (autopilot, regenerate) leaves
it to the take's own job, which queues them and hands itself back to the queue (worker.JobDeferred) until
they are spoken. Then ffmpeg lays them end to end with their pauses and the take renders on ltx23_ia2v, or as
a lip-synced long take once the speech runs past one LTX pass.

A line whose speaker has no voice gets a stand-in from the Voice Gallery (their language, their gender when the
profile says) and the shot says so. Only when every line is plainly English does the take fall back to the video
model's own audio, the lines in quotes in the prompt: LTX speaks English well and babbles in Urdu or Hindi.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app import aerab, longtake, native_audio, voice_gallery
from app import audio_analysis as aa
from app import audio_edit as ae
from app import library as lib
from app import voices as vc
from app.config import get_settings
from app.models import Character, Generation, Job, MediaItem, Shot
from app.services import enqueue_generation, generation_file, job_out
from app.workflows import frames_for

LEAD_S = 0.3  # a breath before the first word, so the mouth doesn't open on frame one
TAIL_S = 0.5
GAP_S = 0.35
MAX_LINES = 12
LINE_MAX = 600
WORDS_PER_S = 2.5
FPS = 24.0
LIVE = ("queued", "generating")
TEST_CLIP_S = (5.0, 10.0)
LONGTAKE_KEYS = ("longtake", "chunks", "assembly", "continuity", "context_frames", "longtake_stats")


class SpeechError(RuntimeError):
    pass


def shot_tag(shot_id: str) -> str:
    # Library tags stop at 40 characters, so the uuid goes in without its dashes (as dialogue.take_tag)
    return f"{lib.SHOT_TAG}{shot_id.replace('-', '')}"


def clip_tag(character_id: str) -> str:
    return f"{lib.CLIP_TAG}{character_id.replace('-', '')}"


def _norm(text) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- the shot's lines

def clean_lines(lines: list[dict] | None, speakers) -> list[dict]:
    """What a PATCH may store: speaker, words and an optional pause. Blank lines go."""
    allowed = set(speakers or [])
    out = []
    for ln in lines or []:
        text = _norm(ln.get("text"))
        if not text:
            continue
        cid = ln.get("character_id") or ""
        if cid not in allowed:
            raise HTTPException(422, "Every line's speaker has to be one of the shot's characters")
        row = {"character_id": cid, "text": text[:LINE_MAX]}
        if ln.get("language"):
            row["language"] = str(ln["language"]).lower()[:12]
        if ln.get("pause_after_s") is not None:
            row["pause_after_s"] = round(max(0.0, min(10.0, float(ln["pause_after_s"]))), 2)
        out.append(row)
    if len(out) > MAX_LINES:
        raise HTTPException(422, f"A shot holds at most {MAX_LINES} lines; split it into more shots")
    return out


def public(lines: list[dict] | None) -> list[dict]:
    return [{k: ln[k] for k in ("character_id", "text", "pause_after_s", "language") if ln.get(k) is not None}
            for ln in lines or []]


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def missing_notice(names: list[str], stand_in: bool = False) -> str:
    verb = "has" if len(names) == 1 else "have"
    if stand_in:
        return (f"{_names(names)} {verb} no voice yet, so a stand-in voice speaks their lines. "
                "Pick a voice in Character Studio.")
    return (f"{_names(names)} {verb} no voice yet, so this shot's lines use the video model's own audio instead "
            "of lip sync. Pick a voice in Character Studio.")


_FEMALE = re.compile(r"\b(female|woman|women|girl|lady|mother|mom|wife|aunt|grandmother|daughter|sister|queen|"
                     r"princess|she|her)\b")
_MALE = re.compile(r"\b(male|man|men|boy|gentleman|father|dad|husband|uncle|grandfather|son|brother|king|prince|"
                   r"he|his)\b")


def _gender(db: Session, cid: str) -> str | None:
    c = db.get(Character, cid) if cid else None
    if c is None:
        return None
    # the profile's own answer first; the description only when it's blank
    for text in (str((c.profile or {}).get("gender") or ""), c.description or ""):
        text = text.lower()
        f, m = _FEMALE.search(text), _MALE.search(text)
        if f and (not m or f.start() < m.start()):
            return "female"
        if m:
            return "male"
    return None


def stand_in(language: str, gender: str | None, key: str) -> str | None:
    """A gallery voice for a speaker who has none, the same one every time for the same character."""
    rows = sorted((v for v in voice_gallery.catalogue().values()
                   if voice_gallery.available(v) and language in (v.get("languages") or [])), key=lambda v: v["id"])
    if gender:
        rows = [v for v in rows if v.get("gender") == gender] or rows
    if not rows:
        return None
    pick = rows[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(rows)]
    return voice_gallery.PREFIX + pick["id"]


def _speaker(db: Session, workspace_id: str, cid: str, cache: dict) -> tuple[str, str | None, bool]:
    """(name, voice id, voice usable) for one character, cached per call."""
    if cid not in cache:
        c = db.get(Character, cid)
        vid = c.voice_id if c is not None else None
        ok = bool(vid) and vc.find_voice(db, workspace_id, vid) is not None
        cache[cid] = (c.name if c is not None else "Someone", vid, ok)
    return cache[cid]


def plan(db: Session, workspace_id: str, lines: list[dict] | None, cache: dict | None = None,
         stand_ins: bool = False) -> dict | None:
    """What a take's speech step works from: each line with its speaker's name and voice. `stand_ins`: never
    leave lines to the video model's own audio while the Voice Gallery has a voice for them."""
    cache = {} if cache is None else cache
    rows, missing = [], []
    for ln in lines or []:
        text = _norm(ln.get("text"))
        if not text:
            continue
        name, vid, ok = _speaker(db, workspace_id, ln.get("character_id") or "", cache)
        if not ok and name not in missing:
            missing.append(name)
        row = {"character_id": ln.get("character_id"), "name": name, "voice_id": vid if ok else None, "text": text}
        if ln.get("language"):
            row["language"] = ln["language"]
        if ln.get("pause_after_s") is not None:
            row["pause_after_s"] = float(ln["pause_after_s"])
        rows.append(row)
    if not rows:
        return None
    native = not stand_ins and bool(missing) and all(
        native_audio.looks_english(r["text"]) and r.get("language") in (None, "en") for r in rows)
    if missing and not native:
        picks = {}
        for i, r in enumerate(rows):
            if r["voice_id"] is None:
                lang = _language(r["text"], None, r.get("language"))
                picks[i] = stand_in(lang, _gender(db, r["character_id"] or ""), r["character_id"] or r["name"])
        # no gallery voice for that language: the old fallback is still better than a failed take
        native = None in picks.values()
        if not native:
            for i, vid in picks.items():
                rows[i].update(voice_id=vid, stand_in=True)
    out = {"status": "pending", "mode": "native" if native else "lipsync", "lines": rows}
    if missing:
        out["notice"] = missing_notice(missing, stand_in=not native)
    return out


def notice_for(db: Session, shot: Shot, cache: dict | None = None) -> str | None:
    p = plan(db, shot.workspace_id, shot.dialogue, cache)
    return p.get("notice") if p else None


def attach(db: Session, shot: Shot, params: dict) -> None:
    """Called from prepare_shot_generation for a take: a fresh plan, whatever the params carried before."""
    for key in ("speech_audio", "lipsync", "audio_start_s", "dialogue", "speech"):
        params.pop(key, None)
    p = plan(db, shot.workspace_id, shot.dialogue)
    if p:
        p["tag"] = shot_tag(shot.id)
        params["dialogue"] = p


def keep_speakers(shot: Shot) -> None:
    # a character taken out of the shot takes their lines along
    have = set(shot.character_ids or [])
    if any(ln.get("character_id") not in have for ln in shot.dialogue or []):
        shot.dialogue = [ln for ln in shot.dialogue or [] if ln.get("character_id") in have]


# ---------------------------------------------------------------- speaking the lines

def _language(text: str, voice, hint: str | None = None) -> str:
    # an Urdu, Arabic or Hindi line says so in its letters; Latin text is spoken in the film's language when
    # the line carries one (contract v4a), else in the voice's own
    found = native_audio.language_of(text)
    if found and found != "en":
        return found
    if hint and hint != "en" and found != "en" and not (hint in aerab.ARABIC_SCRIPT_LANGS and text.isascii()):
        return hint
    lang = (voice.language if voice is not None else None) or "en"
    return "en" if lang in aerab.ARABIC_SCRIPT_LANGS else lang


def _fp(text: str, voice_id: str | None, language: str) -> str:
    return hashlib.sha1(json.dumps([_norm(text), voice_id, language], ensure_ascii=False).encode()).hexdigest()


def _existing(db: Session, workspace_id: str, tag: str) -> dict[str, Generation]:
    """Spoken lines already made for this owner, by fingerprint; a failed one is no use."""
    items = db.scalars(select(MediaItem).where(MediaItem.workspace_id == workspace_id,
                                               cast(MediaItem.tags, String).like(f'%"{tag}"%'))).all()
    ids = [i.generation_id for i in items if i.generation_id]
    gens = db.scalars(select(Generation).where(Generation.id.in_(ids or [""]))
                      .order_by(Generation.created_at.desc())).all()
    out: dict[str, Generation] = {}
    for g in gens:
        fp = ((g.params or {}).get("shot_line") or {}).get("fp")
        if fp and fp not in out and g.status not in ("failed", "rejected"):
            out[fp] = g
    return out


def ensure_takes(db: Session, workspace_id: str, user_id: str | None, d: dict) -> list[Generation]:
    """A speech generation per line of the plan `d`, reusing what's already spoken; records each take on its
    row. Raises HTTPException with the speech code's own message when a line can't be spoken."""
    from app.schemas import SpeakIn

    tag = d["tag"]
    have = None
    who = SimpleNamespace(workspace_id=workspace_id, id=user_id)
    out = []
    for row in d["lines"]:
        voice = vc.find_voice(db, workspace_id, row.get("voice_id"))
        language = _language(row["text"], voice, row.get("language"))
        fp = _fp(row["text"], row.get("voice_id"), language)
        g = db.get(Generation, row["take_id"]) if row.get("take_id") and row.get("fp") == fp else None
        if g is None:
            have = _existing(db, workspace_id, tag) if have is None else have
            g = have.get(fp)
        if g is None:
            body = SpeakIn(text=row["text"], voice_id=row.get("voice_id"), language=language, count=1,
                           title=f"{row['name']}: {lib.short_title(row['text'])}"[:200])
            res = vc.queue_speech(db, who, body, flow="shot_dialogue")
            item = db.get(MediaItem, res["items"][0].id)
            item.tags = [tag]
            g = db.get(Generation, item.generation_id)
            g.params = {**(g.params or {}), "shot_line": {"fp": fp, "tag": tag}}
            have[fp] = g
        row.update(take_id=g.id, fp=fp, language=language)
        out.append(g)
    return out


def queue_lines(db: Session, cur, params: dict) -> list[Job]:
    """The take routes speak the lines now, so they are under way before the take is even claimed."""
    d = params.get("dialogue") or {}
    if d.get("mode") != "lipsync" or d.get("status") != "pending":
        return []
    gens = ensure_takes(db, cur.workspace_id, cur.id, d)
    return [db.get(Job, g.job_id) for g in gens if g.status in LIVE and g.job_id]


def with_lines(prompt: str, rows: list[dict]) -> str:
    """The fallback: LTX voices what it finds in quotes, so the lines go into the motion prompt."""
    said = " ".join(f'{r["name"]} says: "{r["text"]}"' for r in rows if r["text"] not in (prompt or ""))
    return f"{(prompt or '').rstrip()}\n{said}".strip() if said else prompt


def performance(rows: list[dict]) -> str:
    names = list(dict.fromkeys(r["name"] for r in rows))
    verb = "speaks" if len(names) == 1 else "speak"
    return f"{_names(names)} {verb} on camera, the mouth moving with every word, natural small head movements."


# ---------------------------------------------------------------- the speech track

def _files(gens: list[Generation], rows: list[dict]) -> list[Path]:
    out = []
    for g, row in zip(gens, rows):
        path = generation_file(g) if g is not None else None
        if path is None or not path.is_file():
            raise SpeechError(f"{row['name']}'s spoken line is gone; render the take again")
        out.append(path)
    return out


def gaps_of(rows: list[dict]) -> list[float]:
    gaps = [float(r["pause_after_s"]) if r.get("pause_after_s") is not None else GAP_S for r in rows]
    if gaps:
        gaps[-1] = 0.0
    return gaps


def track_graph(n: int, gaps: list[float], total_s: float) -> str:
    """The lines back to back with their pauses, a short lead-in, padded (or cut) to the take's length."""
    parts = []
    for i in range(n):
        f = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono"
        if gaps[i] > 0:
            f += f",apad=pad_dur={gaps[i]:.3f}"
        parts.append(f"[{i}:a]{f}[p{i}]")
    joined = "".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cat]"
    fit = f"adelay=delays={round(LEAD_S * 1000)}:all=1,apad=whole_dur={total_s:.4f},atrim=end={total_s:.4f}"
    return ";".join([*parts, joined, f"[cat]{fit}[out]"])


def render_track(files: list[Path], gaps: list[float], total_s: float, dst: Path, tick=None) -> None:
    args: list[str] = []
    for p in files:
        args += ["-i", str(p)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.stem + ".part.wav")
    ae.run([*args, "-filter_complex", track_graph(len(files), gaps, total_s), "-map", "[out]", "-ar", "48000",
            "-ac", "1", "-c:a", "pcm_s16le", str(part)], tick=tick)
    part.replace(dst)


def fit_duration(asked: float, speech_s: float, d: dict) -> float:
    need = round(speech_s + TAIL_S, 2)
    lo, hi = d.get("min_s"), d.get("max_s")
    if hi is None:
        cap = get_settings().longtake_max_s
        if need > cap:
            raise SpeechError(f"The lines run {speech_s:.1f} s, and a take can be at most {cap:g} s. "
                              "Shorten them or split the shot.")
    dur = max(float(asked or 0), need, float(lo or 0))
    return round(min(dur, float(hi)) if hi is not None else dur, 2)


def timing(params: dict, dur: float, *, allow_long: bool) -> None:
    """num_frames (and the long-take plan) for the stretched duration. A lip-synced long take always
    continues from the previous chunk's frames: the i2v fallback has no way to carry the audio over."""
    params.update(duration_s=dur, fps=params.get("fps") or FPS, num_frames=frames_for(dur, FPS))
    if allow_long and longtake.is_long(dur):
        overlap = longtake.context_frames("extend")
        params.update(longtake=True, continuity="extend", context_frames=overlap,
                      chunks=longtake.plan_chunks(dur, overlap=overlap), assembly={"status": "pending"})
    else:
        for k in LONGTAKE_KEYS:
            params.pop(k, None)
        params["num_frames"] = min(params["num_frames"], longtake.SINGLE_MAX_FRAMES)


# ---------------------------------------------------------------- inside the take's job

def _wait(ctx, live: list[Generation]) -> None:
    """Hand the take back to the queue until its lines are spoken; poll a little first when this worker has
    nothing else to do, the same way the autopilot waits on its children."""
    from app import autopilot as ap
    from app import worker

    db, job = ctx.db, ctx.job
    ids = [g.job_id for g in live if g.job_id]
    for j in db.scalars(select(Job).where(Job.id.in_(ids or [""]))).all():
        j.payload = {**(j.payload or {}), "parent_job_id": job.id}
    job.result = {"waiting_on": ids}
    n = len(live)
    msg = f"Waiting for {n} spoken line{'s' if n != 1 else ''}"
    ctx.progress(0.01, msg)
    lanes = getattr(ctx, "lanes", None)

    def other_work() -> bool:
        stmt = select(Job.id).where(Job.status == "queued", Job.priority > ap.WAIT_PRIORITY, Job.id != job.id)
        if lanes:
            stmt = stmt.where(Job.lane.in_(lanes))
        return db.scalars(stmt.limit(1)).first() is not None

    if not other_work():
        deadline = time.monotonic() + ap.POLL_S
        while time.monotonic() < deadline:
            time.sleep(1.0)
            ctx.progress(0.01, msg)
            for g in live:
                db.refresh(g)
            if not any(g.status in LIVE for g in live):
                return
            if other_work():
                break
    db.commit()
    raise worker.JobDeferred(msg, priority=ap.WAIT_PRIORITY)


def run_in_job(ctx, gen: Generation) -> None:
    """The take's speech pre-step (worker.handle_generate). Returns once the take's params are final; raises
    JobDeferred while lines are still being spoken."""
    d = dict((gen.params or {}).get("dialogue") or {})
    if d.get("status") != "pending":
        return
    db = ctx.db
    rows = [dict(r) for r in d.get("lines") or []]
    d["lines"] = rows
    if d.get("mode") != "lipsync":
        gen.prompt = with_lines(gen.prompt, rows)
        gen.params = {**gen.params, "dialogue": {**d, "status": "native"}}
        db.commit()
        return

    d.setdefault("tag", shot_tag(gen.target_id))
    user = ((gen.params or {}).get("created_by") or {}).get("user_id")
    try:
        gens = ensure_takes(db, gen.workspace_id, user, d)
    except HTTPException as e:
        raise SpeechError(f"Couldn't speak the shot's lines: {e.detail}") from None
    gen.params = {**gen.params, "dialogue": d}
    db.commit()

    live = [g for g in gens if g.status in LIVE]
    if live:
        _wait(ctx, live)  # back here only when they all finished while it polled
    for g, row in zip(gens, rows):
        if g.status not in ("ready", "approved"):
            j = db.get(Job, g.job_id) if g.job_id else None
            why = f" ({j.error})" if j is not None and j.error else ""
            raise SpeechError(f"{row['name']}'s line couldn't be spoken{why}. Check the voice and render the take again.")

    ctx.progress(0.02, "Laying the lines on the speech track")
    files = _files(gens, rows)
    gaps = gaps_of(rows)
    speech_s = LEAD_S + sum(aa.stream_info(f).duration or 0.0 for f in files) + sum(gaps)
    params = dict(gen.params)
    dur = fit_duration(params.get("duration_s") or 0, speech_s, d)
    folder, rel = longtake.take_dir(gen)
    dst = folder / "speech.wav"
    render_track(files, gaps, dur, dst, tick=lambda: ctx.progress(0.02, "Laying the lines on the speech track"))
    timing(params, dur, allow_long=d.get("max_s") is None)
    params.update(lipsync=True, speech_audio=str(dst), audio_start_s=0.0,
                  speech={"file": f"{rel}/speech.wav", "duration_s": round(speech_s, 3)},
                  dialogue={**d, "status": "ready"})
    gen.params = params
    if gen.prompt and performance(rows) not in gen.prompt:
        gen.prompt = f"{gen.prompt.rstrip()}\n{performance(rows)}"
    if gen.target_type == "shot":
        _stretch(db, gen.target_id, dur)
    db.commit()


def _stretch(db: Session, shot_id: str, dur: float) -> None:
    shot = db.get(Shot, shot_id)
    if shot is None or shot.duration_s >= dur - 0.01:
        return
    # the lines set the shot's length; beats timed to the old length get rewritten
    shot.duration_s = dur
    longtake.mark_beats_stale(shot)
    if longtake.is_long(dur):
        shot.shot_type = "long_take"


# ---------------------------------------------------------------- Character Studio test clip

def _aspect(path: Path | None) -> str:
    from PIL import Image

    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return "1:1"
    r = w / h if h else 1.0
    return "16:9" if r >= 1.3 else "9:16" if r <= 0.77 else "1:1"


def queue_test_clip(db: Session, cur, c: Character, text: str, quality: str = "standard", finish: str | None = None,
                    engine: str | None = None) -> dict:
    """A 5-10 s lip-synced talking clip of the character saying `text`, from the sheet's close-up (else the
    portrait). A Library video item; the line itself is a hidden take tagged clip:<id>."""
    from app import character_sheet as cs

    text = _norm(text)
    if not text:
        raise HTTPException(422, "Type a line for the character to say")
    name, vid, ok = _speaker(db, cur.workspace_id, c.id, {})
    if not ok:
        raise HTTPException(409, f"{c.name} has no voice yet. Pick one on the Voice tab first.")
    words = len(text.split())
    if words / WORDS_PER_S + LEAD_S + TAIL_S > TEST_CLIP_S[1]:
        raise HTTPException(422, f"A test clip is at most {TEST_CLIP_S[1]:g} seconds; keep the line under about "
                                 f"{int((TEST_CLIP_S[1] - LEAD_S - TAIL_S) * WORDS_PER_S)} words")
    source = cs.panel_generation(db, c.id, "closeup")
    panel = "closeup"
    if source is None:
        source, panel = cs.portrait(db, c.id), "portrait"
    if source is None:
        raise HTTPException(409, f"{c.name} needs a portrait (or the sheet's close-up) first")

    d = {"status": "pending", "mode": "lipsync", "tag": clip_tag(c.id), "min_s": TEST_CLIP_S[0],
         "max_s": TEST_CLIP_S[1], "lines": [{"character_id": c.id, "name": name, "voice_id": vid, "text": text}]}
    ensure_takes(db, cur.workspace_id, cur.id, d)
    item = MediaItem(workspace_id=cur.workspace_id, kind="video", origin="generated", title=f"{c.name} · test clip",
                     tags=["test clip"], home="character")
    db.add(item)
    db.flush()
    who = f"{c.name}, {c.description.strip()}" if c.description.strip() else c.name
    said = text.replace('"', "'")
    from app.character_looks import performance_text

    # the character's own way of moving (Character Looks), else the plain default
    manner = performance_text(c) or "Natural expression, small head movements."
    prompt = (f'Close-up of {who}, looking into the camera and saying: "{said}" The mouth moving with every word, '
              f"blinking. {manner} Steady camera, soft light.")
    params = {"first_frame_id": source.id, "duration_s": TEST_CLIP_S[0], "fps": FPS,
              "aspect_ratio": _aspect(generation_file(source)), "dialogue": d,
              "test_clip": {"character_id": c.id, "panel": panel}, "quality": quality,
              "created_by": {"user_id": cur.id, "flow": "character_test_clip"}}
    g = enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="media", target_id=item.id,
                           kind="video", prompt=prompt, params={**params, **({"s2v_finish": finish} if finish else {}),
                                   **({"lipsync_engine": engine} if engine else {})})
    item.generation_id = g.id
    cs.put(c, test_clip={"media_id": item.id, "text": text, "panel": panel})
    speech = [db.get(Generation, r["take_id"]) for r in d["lines"]]
    return {"item": item, "job": job_out(db.get(Job, g.job_id)),
            "speech_jobs": [job_out(db.get(Job, s.job_id)) for s in speech if s and s.status in LIVE and s.job_id]}
