"""Poetry Short records (contract v33): settings, stage records, the poem's lines, the scene layout and
the shape the API hands back.

A Short's song, keyframes and clips are ordinary generations: the song is a Library audio item, keyframes
and clips belong to their scene (target_type "poetry_scene", target_id the scene's id), and the finished
video is a Library video item whose versions are the renders. The .srt and the small preview of each render
sit in workspaces/<ws>/poetry/<short id>/.
"""
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import aerab
from app import montage as mt
from app.config import get_settings
from app.models import Generation, Job, MediaItem, PoetryShort, new_id
from app.services import gen_out, job_out, media_url

JOB = "poetry_short"
RENDER_JOB = "poetry_render"
STAGES = (("poem", "Poem"), ("song", "Song"), ("timing", "Timing"), ("scenes", "Scenes"), ("render", "Render"))
STAGE_KEYS = tuple(k for k, _ in STAGES)
ASPECTS = ("9:16", "16:9", "1:1")
QUALITIES = ("draft", "final")
IMAGE_MODELS = ("krea2_turbo", "qwen_image_2512", "zimage_turbo")
LIVE = ("queued", "generating")
FINISHED = ("ready", "approved")
ATTEMPTS = 2  # first try plus one re-roll, for the song, keyframes and clips alike
# a 5-minute Episode gets ~33 scenes; a 60 s Short still gets 7
MIN_SCENES, MAX_SCENES, SCENE_S = 4, 34, 9.0
# an intro or outro picture holds this long; a clip plays up to BOOKEND_MAX_S of itself
BOOKEND_S, BOOKEND_MAX_S = 4.0, 10.0
TITLE_S = 3.0
FADE_S = 0.45
SONG_MODEL = "ace15_turbo"

DEFAULTS = {"topic": "", "mood": "", "style": "", "vocal": "female", "length_s": 60.0, "character_id": None,
            "character": "", "setting": "", "aspect": "9:16", "quality": "draft", "image_model": "krea2_turbo",
            "title_card": False, "review": True, "whisper": True,
            "caption_style": "classic", "sing_voice_id": None, "intro_media_id": None, "outro_media_id": None,
            "lead_voice": "girl", "group_size": 3, "look_id": None}

# "[verse]", "[Chorus 2]", and "[verse: lead]" for who sings it (app.song_parts)
_MARKER = re.compile(r"^\s*\[\s*([A-Za-z][A-Za-z -]*?)\s*\d*\s*(?::\s*[A-Za-z]+\s*)?\]\s*$")


class PoetryError(RuntimeError):
    pass


def settings_of(ps: PoetryShort) -> dict:
    return {**DEFAULTS, **(ps.settings or {})}


# ---------------------------------------------------------------- stages

def stage(ps: PoetryShort, key: str) -> dict:
    return dict((ps.stages or {}).get(key) or {"status": "pending"})


def put_stage(ps: PoetryShort, key: str, rec: dict) -> None:
    ps.stages = {**(ps.stages or {}), key: rec}
    flag_modified(ps, "stages")


def reset_from(ps: PoetryShort, key: str) -> None:
    """`key` and every later stage back to pending; what they made stays until it's remade."""
    for k in STAGE_KEYS[STAGE_KEYS.index(key):]:
        rec = stage(ps, k)
        rec.update(status="pending", detail=None)
        rec.pop("run", None)
        put_stage(ps, k, rec)
    if ps.stage not in STAGE_KEYS or STAGE_KEYS.index(ps.stage) > STAGE_KEYS.index(key):
        ps.stage = key


def done(ps: PoetryShort, key: str) -> bool:
    return stage(ps, key).get("status") in ("done", "skipped")


def busy(db: Session, ps: PoetryShort) -> Job | None:
    j = db.get(Job, ps.job_id) if ps.job_id else None
    return j if j is not None and j.status in ("queued", "running") else None


def ensure_idle(db: Session, ps: PoetryShort) -> None:
    if busy(db, ps) is not None:
        raise HTTPException(409, "The Short is working; wait for it, or cancel the job")


def queue(db: Session, ps: PoetryShort, run: str = "continue") -> Job:
    ensure_idle(db, ps)
    job = Job(workspace_id=ps.workspace_id, type=JOB, message="Waiting for a worker · Poetry Short",
              payload={"poetry_id": ps.id, "run": run})
    db.add(job)
    db.flush()
    ps.job_id = job.id
    return job


# ---------------------------------------------------------------- the poem

def parse_lines(lyrics: str) -> list[dict]:
    """Sung lines with the section they sit in; [verse] / [chorus] markers and blank lines aren't lines."""
    out, section, count = [], "verse", {}
    for raw in (lyrics or "").splitlines():
        m = _MARKER.match(raw)
        if m:
            section = m[1].strip().lower().replace(" ", "-")
            count[section] = count.get(section, 0) + 1
            continue
        text = " ".join(raw.split())
        if text:
            n = count.setdefault(section, 1)
            out.append({"text": text, "section": section, "part": f"{section}:{n}", "start": None, "end": None})
    return out


def timed(ps: PoetryShort) -> bool:
    return bool(ps.lines) and all(ln.get("start") is not None and ln.get("end") is not None for ln in ps.lines)


def song_duration(ps: PoetryShort) -> float | None:
    d = (ps.song or {}).get("duration_s")
    return float(d) if d else None


# ---------------------------------------------------------------- scenes

def bookend_source(db, workspace_id: str, media_id: str, which: str = "intro"):
    """The Library picture or clip shown before (intro) or after (outro) the song: (generation, kind)."""
    from fastapi import HTTPException

    from app.models import MediaItem

    item = db.get(MediaItem, media_id or "")
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(422, f"The {which} isn't in this workspace's Library")
    if item.kind not in ("image", "video"):
        raise HTTPException(422, f"The {which} has to be a picture or a video clip")
    g = db.get(Generation, item.generation_id or "")
    if g is None or g.status not in FINISHED or not g.file_path:
        raise HTTPException(422, f"The {which} isn't finished yet")
    return g, item.kind


def group_scenes(starts: list[float], duration: float) -> list[int]:
    """The first line of each scene: 4 to 28 scenes of about SCENE_S, cut where a line starts."""
    n = len(starts)
    if n == 0:
        return []
    want = max(MIN_SCENES, min(MAX_SCENES, round(duration / SCENE_S)))
    want = min(want, n)
    firsts, prev = [0], 0
    for k in range(1, want):
        ideal = duration * k / want
        # leave a line for every scene still to come
        options = range(prev + 1, n - (want - k) + 1)
        if not options:
            break
        prev = min(options, key=lambda i: abs(starts[i] - ideal))
        firsts.append(prev)
    return firsts


def scene_windows(ps: PoetryShort, duration: float | None = None) -> list[tuple[float, float]]:
    """Each scene's stretch of the song: it opens on its first line (the first one at 0) and runs to the
    next scene, the last to the end of the song. Follows the current line timings, edits included."""
    duration = duration or song_duration(ps) or 0.0
    out = []
    scenes = ps.scenes or []
    for k, sc in enumerate(scenes):
        first = int(sc["first"])
        a = 0.0 if k == 0 else float(ps.lines[first].get("start") or 0.0)
        out.append(a)
    wins = []
    for k, a in enumerate(out):
        b = out[k + 1] if k + 1 < len(out) else duration
        wins.append((round(a, 3), round(max(b, a + 1.0), 3)))
    return wins


def scene_lines(ps: PoetryShort, k: int) -> list[str]:
    scenes = ps.scenes or []
    first = int(scenes[k]["first"])
    last = int(scenes[k + 1]["first"]) - 1 if k + 1 < len(scenes) else len(ps.lines) - 1
    return [ln["text"] for ln in ps.lines[first:last + 1]]


def new_scene(n: int, first: int) -> dict:
    return {"id": new_id(), "n": n, "first": first, "image_prompt": "", "motion_prompt": "", "keyframe": None,
            "keyframe_tries": 0, "clip": None, "clip_tries": 0, "clip_from": None, "error": None}


def put_scene(ps: PoetryShort, sc: dict) -> None:
    ps.scenes = [sc if s["id"] == sc["id"] else s for s in ps.scenes or []]
    flag_modified(ps, "scenes")


def scene_status(kf: Generation | None, clip: Generation | None, sc: dict) -> str:
    if kf is not None and kf.status in LIVE:
        return "keyframe"
    if clip is not None and clip.status in LIVE:
        return "clip"
    if clip is not None and clip.status in FINISHED and sc.get("clip_from") == (kf.id if kf else None):
        return "done"
    if (kf is not None and kf.status == "failed") or (clip is not None and clip.status == "failed"):
        return "failed"
    return "pending"


# ---------------------------------------------------------------- files

def files_dir(ps: PoetryShort) -> tuple[Path, str]:
    rel = Path("workspaces") / ps.workspace_id / "poetry" / ps.id
    return get_settings().data_dir / rel, rel.as_posix()


# ---------------------------------------------------------------- output

def _gen(db: Session, gid: str | None) -> dict | None:
    g = db.get(Generation, gid) if gid else None
    return gen_out(g).model_dump(mode="json") if g is not None else None


def status_of(db: Session, ps: PoetryShort) -> tuple[str, str | None]:
    if busy(db, ps) is not None:
        return "running", None
    for k in STAGE_KEYS:
        if stage(ps, k).get("status") == "review":
            return "review", k
    if any(stage(ps, k).get("status") == "failed" for k in STAGE_KEYS):
        return "failed", None
    return ("done" if done(ps, "render") else "idle"), None


def outputs_out(db: Session, ps: PoetryShort) -> dict | None:
    o = dict(ps.outputs or {})
    if not o.get("media_id"):
        return None
    _, rel = files_dir(ps)
    g = db.get(Generation, o.get("generation_id") or "")
    return {**{k: o.get(k) for k in ("media_id", "generation_id", "duration_s", "width", "height", "chapters",
                                       "built_at", "renderer", "fonts_missing")},
            "video_url": media_url(g.file_path) if g is not None and g.status in FINISHED else None,
            "preview_url": media_url(f"{rel}/{o['preview']}") if o.get("preview") else None,
            "srt_url": media_url(f"{rel}/{o['srt']}") if o.get("srt") else None,
            # timings or scenes changed since this render
            "stale": not done(ps, "render")}


def short_out(db: Session, ps: PoetryShort) -> dict:
    status, review = status_of(db, ps)
    wins = scene_windows(ps) if ps.scenes and timed(ps) else []
    scenes = []
    for k, sc in enumerate(ps.scenes or []):
        kf, clip = db.get(Generation, sc.get("keyframe") or ""), db.get(Generation, sc.get("clip") or "")
        last = int(ps.scenes[k + 1]["first"]) - 1 if k + 1 < len(ps.scenes) else len(ps.lines) - 1
        scenes.append({"id": sc["id"], "n": k + 1, "first": sc["first"], "last": last,
                       "start": wins[k][0] if wins else None, "end": wins[k][1] if wins else None,
                       "image_prompt": sc.get("image_prompt") or "", "motion_prompt": sc.get("motion_prompt") or "",
                       "status": scene_status(kf, clip, sc), "error": sc.get("error"),
                       "keyframe": _gen(db, sc.get("keyframe")), "clip": _gen(db, sc.get("clip"))})
    song = dict(ps.song or {})
    job = db.get(Job, ps.job_id) if ps.job_id else None
    st = settings_of(ps)
    return {
        "id": ps.id, "title": ps.title, "language": ps.language, "stage": ps.stage, "status": status,
        "review": review,
        "stages": [{"key": k, "label": label, "status": stage(ps, k).get("status", "pending"),
                    "detail": stage(ps, k).get("detail"), "started_at": stage(ps, k).get("started_at"),
                    "finished_at": stage(ps, k).get("finished_at")} for k, label in STAGES],
        "settings": st,
        "poem": {"lyrics": (ps.poem or {}).get("lyrics") or "", "tags": (ps.poem or {}).get("tags") or "",
                 "own": bool((ps.poem or {}).get("own"))},
        "lines": [{"i": i, "text": ln["text"], "section": ln.get("section") or "verse", "part": ln.get("part"),
                   "start": ln.get("start"), "end": ln.get("end")} for i, ln in enumerate(ps.lines or [])],
        "song": {"media_id": song.get("media_id"), "duration_s": song.get("duration_s"),
                 "generation": _gen(db, song.get("generation_id"))},
        "timing": {"source": (ps.timing or {}).get("source"), "edited": bool((ps.timing or {}).get("edited")),
                   "phrases": (ps.timing or {}).get("phrases") or [], "separated": (ps.timing or {}).get("separated")},
        "scenes": scenes,
        "outputs": outputs_out(db, ps),
        "job": job_out(job).model_dump(mode="json") if job is not None else None,
        "created_at": ps.created_at, "updated_at": ps.updated_at,
    }


def summary_out(db: Session, ps: PoetryShort) -> dict:
    status, review = status_of(db, ps)
    first = next((s for s in ps.scenes or [] if s.get("keyframe")), None)
    kf = db.get(Generation, first["keyframe"]) if first else None
    return {"id": ps.id, "title": ps.title, "language": ps.language, "stage": ps.stage, "status": status,
            "review": review, "aspect": settings_of(ps)["aspect"], "lines": len(ps.lines or []),
            "duration_s": song_duration(ps), "thumb_url": gen_out(kf).thumb_url if kf is not None else None,
            "media_id": (ps.outputs or {}).get("media_id"), "updated_at": ps.updated_at}


# ---------------------------------------------------------------- making one

def create(db: Session, cur, body: dict) -> PoetryShort:
    st = {k: body[k] for k in DEFAULTS if k in body and body[k] is not None}
    own = (body.get("poem") or "").strip()
    ps = PoetryShort(id=new_id(), workspace_id=cur.workspace_id, title=(body.get("title") or "").strip()[:200],
                     language=(body.get("language") or "ur").lower(), stage="poem", stages={}, settings=st,
                     poem={"own": bool(own), "lyrics": own}, lines=[], song={}, timing={}, scenes=[], outputs={},
                     created_by=cur.id)
    db.add(ps)
    db.flush()
    return ps


def library_version(db: Session, ps: PoetryShort) -> tuple[MediaItem, Generation]:
    from app import library as lib
    from app.services import new_seed, next_version

    st = settings_of(ps)
    item = db.get(MediaItem, (ps.outputs or {}).get("media_id") or "")
    w, h = mt.SIZES[st["aspect"]]
    if item is None or item.workspace_id != ps.workspace_id:
        item = MediaItem(id=new_id(), workspace_id=ps.workspace_id, kind="video", origin="generated",
                         title=(ps.title or "Poetry Short")[:300], tags=["poetry", "short"], width=w, height=h)
        db.add(item)
        db.flush()
    g = Generation(id=new_id(), workspace_id=ps.workspace_id, project_id=None, target_type="media",
                   target_id=item.id, kind="video", version=next_version(db, "media", item.id, "video"),
                   status="queued", prompt=ps.title or "Poetry Short", seed=new_seed(), parent_id=item.generation_id,
                   params={"poetry_id": ps.id, "has_audio": True, "aspect_ratio": st["aspect"],
                           "created_by": {"user_id": ps.created_by, "flow": "poetry_short"}})
    db.add(g)
    db.flush()
    if item.generation_id is None:
        item.generation_id = g.id
    lib.touch(item)
    return item, g


def wants_aerab(ps: PoetryShort, text: str) -> bool:
    return get_settings().urdu_aerab and aerab.applies(ps.language, text)
