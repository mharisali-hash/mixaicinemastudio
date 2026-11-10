"""Image Edit sets: one instruction made into up to 25 separate pictures, each its own variation.

People ask the edit model for "a sheet of 25 expressions" and get one picture with 25 small faces. A set
makes the 25 pictures instead. The request makes every tile up front (each with a waiting generation and
no job yet) plus this one parent job, so the set is a single job for the jobs-at-once limit. The parent has
the writer turn the instruction into one line per picture (built-in lists when it can't), then keeps a
couple of the pictures on the image queue at a time, like the bulk orchestrator does. Each picture is an
ordinary generate job with parent_job_id, so the content and rights checks still run on every one.
"""
import logging
import re
import time
from dataclasses import replace

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import quota
from app.camera import ANGLES, SHOT_SIZES
from app.character_panels import KEEP
from app.config import get_settings
from app.models import Folder, Generation, Job, MediaItem, User, new_id, utcnow

log = logging.getLogger("mixai.edit_set")

JOB = "edit_set"
POLL_S = 10.0
LIVE_JOB = ("queued", "running")
FINISHED = ("ready", "approved")

# the writer's lines get this; the list lines say more because they only change face, camera and light
SAME = "Keep it the same person, with the same face and identity."

EXPRESSIONS = [
    "a calm neutral expression", "a warm genuine smile", "a serious, focused look",
    "surprise, eyebrows raised and mouth slightly open", "a sad, downcast look",
    "anger, brows drawn together and jaw tight", "a worried, uneasy look", "a determined, resolute look",
    "a big open laugh", "a soft, shy smile", "a thoughtful look, eyes drifting away", "a confident smirk",
    "a tired, weary look", "a playful wink", "a sceptical look with one eyebrow raised", "quiet contentment",
    "fear, eyes wide", "disgust, nose wrinkled", "a dreamy, faraway gaze", "a proud look, chin lifted",
    "embarrassment, a sheepish grin", "relief, a long exhale", "curiosity, head slightly tilted",
    "a stern, cold stare", "joyful excitement",
]
# what fits a head-and-shoulders set: over-the-shoulder and point-of-view need someone else in the frame
CAMERA_SIZES = ["cu", "mcu", "ms", "ecu"]
CAMERA_ANGLES = ["eye", "low", "high", "dutch", "overhead"]
LIGHTING = [
    "Rembrandt lighting with a small triangle of light on the far cheek",
    "soft butterfly lighting from just above the lens",
    "dramatic split lighting, half the face in shadow",
    "a strong rim light from behind against a dark background",
    "warm golden-hour sunlight through a window",
    "neon-noir light in teal and magenta",
    "low-key chiaroscuro with deep shadows",
    "bright, soft high-key light",
    "warm flickering candlelight",
    "cool blue moonlight",
    "soft overcast daylight",
    "hard sunlight casting venetian-blind shadows",
]
_SHEET_WORDS = re.compile(r"\b(sheets?|grids?|collages?|composites?|panels?|contact sheet|mosaic)\b", re.I)


def fallback_variants(n: int) -> list[str]:
    """n different one-picture lines from the built-in lists, no two alike (the expressions alone don't repeat
    up to 25; past that the other lists make the difference)."""
    out = []
    for i in range(n):
        size = SHOT_SIZES[CAMERA_SIZES[i % len(CAMERA_SIZES)]][3]
        angle = ANGLES[CAMERA_ANGLES[i % len(CAMERA_ANGLES)]][3]
        light = LIGHTING[i % len(LIGHTING)]
        out.append(f"Same person, now with {EXPRESSIONS[i % len(EXPRESSIONS)]}. Shot as a {size}, {angle}, "
                   f"lit with {light}. {KEEP}")
    return out


def set_name(text: str) -> str:
    low = text.lower()
    for word in ("expression", "angle", "pose", "outfit", "hairstyle", "background", "lighting", "emotion"):
        if word in low:
            what = "Lighting" if word == "lighting" else f"{word.capitalize()}s"
            break
    else:
        from app.library import short_title

        # "a grid of 9 cats in hats" -> "Cats in hats"
        rest = re.sub(r"\b(a|an|the|of|make|create|\d+)\b", " ", _SHEET_WORDS.sub(" ", text), flags=re.I)
        what = short_title(rest, 3)
        what = what[:1].upper() + what[1:]
    return f"{what} set · {utcnow():%d %b}"


# ---------------------------------------------------------------- the request

def create(db: Session, workspace_id: str, *, n: int, title: str, text: str, preamble: str, params: dict,
           seed: int, into: str | None) -> tuple[Job, list[MediaItem]]:
    """The folder, n waiting tiles and the parent job. Nothing is queued for the GPU until the parent runs."""
    from app import organise

    folder = organise.new_folder(db, workspace_id, set_name(text)[:120], "image",
                                 parent=db.get(Folder, into) if into else None)
    job = Job(workspace_id=workspace_id, type=JOB, message=f"Writing {n} different versions…")
    db.add(job)
    db.flush()
    items, gens = [], []
    for i in range(n):
        item = MediaItem(id=new_id(), workspace_id=workspace_id, kind="image", origin="generated",
                         title=f"{title} · {i + 1}"[:300], tags=[], width=params.get("width"),
                         height=params.get("height"), folder_id=folder.id)
        db.add(item)
        db.flush()
        g = Generation(workspace_id=workspace_id, project_id=None, target_type="media", target_id=item.id,
                       kind="image", version=1, status="queued", prompt=text,
                       params={**params, "edit_set": {"job_id": job.id, "index": i + 1, "of": n}},
                       seed=(seed + i) % 2**31)
        db.add(g)
        db.flush()
        item.generation_id = g.id
        items.append(item)
        gens.append(g.id)
    job.payload = {"generation_ids": gens, "folder_id": folder.id, "instruction": text, "preamble": preamble}
    return job, items


# ---------------------------------------------------------------- writing the versions

class _Versions(BaseModel):
    versions: list[str] = Field(default_factory=list)


def _messages(instruction: str, n: int) -> list[dict]:
    return [
        {"role": "system", "content": (
            f"You plan a set of {n} separate pictures, each made by editing the same photo. The request below may "
            "ask for a sheet, grid or collage; ignore that, because every version becomes its own full picture. "
            f"Write exactly {n} short edit instructions, one per picture. Each one describes a single picture of "
            "the same person or subject, changes only what the request wants varied, keeps every other wish in "
            "the request, and is clearly different from all the others. Never mention a sheet, grid, collage, "
            "panels or more than one picture. One or two plain sentences each. "
            'Answer as JSON: {"versions": ["...", "..."]}')},
        {"role": "user", "content": instruction},
    ]


def _clean(lines: list[str]) -> list[str]:
    out, seen = [], set()
    for line in lines:
        line = " ".join(str(line).split()).strip(" -•")
        key = line.lower().rstrip(".")
        if not line or key in seen or _SHEET_WORDS.search(line):
            continue
        seen.add(key)
        out.append(f"{line.rstrip('.')}. {SAME}")
    return out


def write_lines(instruction: str, n: int, tick=None) -> tuple[list[str], str]:
    """n instructions and who wrote them: "writer", "lists", or "writer+lists" when it came up short."""
    from app.llm import LLMError, chat_sync

    got: list[str] = []
    try:
        res = chat_sync("creative", _messages(instruction, n), schema=_Versions, think=False, temperature=0.8,
                        max_tokens=300 + 90 * n, tick=tick)
        got = _clean(res.data.versions)[:n]
    except (LLMError, ValueError) as e:
        log.warning("edit set: the writer couldn't plan %d versions (%s); using the lists", n, e)
    if len(got) >= n:
        return got, "writer"
    rest = [line for line in fallback_variants(n) if line not in got][:n - len(got)]
    return got + rest, "writer+lists" if got else "lists"


# ---------------------------------------------------------------- the parent job

def _w():
    from app import worker

    return worker


def _gens(db: Session, payload: dict | None) -> list[tuple[int, Generation]]:
    ids = (payload or {}).get("generation_ids") or []
    rows = {g.id: g for g in db.scalars(select(Generation).where(Generation.id.in_(ids)))} if ids else {}
    return [(i, rows[gid]) for i, gid in enumerate(ids) if gid in rows]


def _child(db: Session, g: Generation) -> Job | None:
    return db.get(Job, g.job_id) if g.job_id else None


def _waiting(g: Generation) -> bool:
    return g.job_id is None and g.status == "queued"


def _start(db: Session, parent: Job, g: Generation, line: str) -> Job:
    from app import brand

    p = parent.payload or {}
    instruction = f"{p.get('preamble') or ''}{line}"
    prompt = instruction
    kit_id = ((g.params or {}).get("brand") or {}).get("kit_id")
    kit = brand.get_kit(db, g.workspace_id, kit_id) if kit_id else None
    suffix = brand.image_prompt_suffix(kit) if kit is not None else ""
    if suffix:
        prompt = f"{prompt.rstrip('.')}. {suffix}"
    g.prompt = prompt
    # the picture's details show its own line, so any one of them can be remade or edited on its own
    g.params = {**(g.params or {}), "instruction": instruction, "user_prompt": line}
    child = Job(workspace_id=g.workspace_id, type="generate", generation_id=g.id, user_id=parent.user_id,
                payload={"generation_id": g.id, "parent_job_id": parent.id}, message="Waiting for a worker")
    db.add(child)
    db.flush()
    g.job_id = child.id
    return child


def _check_minutes(db: Session, job: Job) -> None:
    user = db.get(User, job.user_id) if job.user_id else None
    if user is None:
        return
    # the set already is the one job at once it counts as; only the minutes can stop it now
    lim = quota.limits_for(db, user)
    quota.check_minutes(db, user, replace(lim, max_queued=None), utcnow())


def _stop_rest(db: Session, parent: Job, gens: list[tuple[int, Generation]], e: quota.QuotaExceeded) -> str:
    why = "Stopped: out of this month's minutes" if e.body.get("reason") == "month" \
        else "Stopped: out of today's minutes"
    now = utcnow()
    for _, g in gens:
        if not _waiting(g):
            continue
        # a finished-looking job of its own, so the tile can say why
        child = Job(workspace_id=g.workspace_id, type="generate", generation_id=g.id, user_id=parent.user_id,
                    payload={"generation_id": g.id, "parent_job_id": parent.id}, status="failed", error=why,
                    message="Stopped", finished_at=now)
        db.add(child)
        db.flush()
        g.job_id = child.id
        g.status = "failed"
    return why


def _other_work(ctx) -> bool:
    from app.autopilot import WAIT_PRIORITY

    lanes = getattr(ctx, "lanes", None)
    lane_ok = [Job.lane.in_(lanes)] if lanes else []
    return bool(ctx.db.scalar(select(func.count()).select_from(Job).where(
        Job.status == "queued", Job.priority > WAIT_PRIORITY, Job.id != ctx.job.id, *lane_ok)))


def handle(ctx) -> dict:
    from app.autopilot import WAIT_PRIORITY

    db, job = ctx.db, ctx.job
    gens = _gens(db, job.payload)
    if not gens:
        raise RuntimeError("The pictures of this set have been deleted")
    n = len((job.payload or {}).get("generation_ids") or [])
    r = dict(job.result or {})
    if not r.get("lines"):
        msg = f"Writing {n} different versions…"
        ctx.progress(0.02, msg)
        lines, by = write_lines((job.payload or {}).get("instruction") or "", n, tick=lambda: ctx.progress(0.02, msg))
        # kept on the job, so a retry or a restart doesn't write them again
        r.update(lines=lines, written_by=by)
        job.result = dict(r)
        db.commit()
    lines = r["lines"]
    want = max(1, get_settings().edit_set_in_flight)
    while True:
        db.expire_all()
        gens = _gens(db, job.payload)
        live = [g for _, g in gens if g.job_id and (c := _child(db, g)) is not None and c.status in LIVE_JOB]
        for i, g in gens:
            if len(live) >= want or r.get("stopped"):
                break
            if not _waiting(g):
                continue
            try:
                _check_minutes(db, job)
            except quota.QuotaExceeded as e:
                r["stopped"] = _stop_rest(db, job, gens, e)
                db.commit()
                break
            _start(db, job, g, lines[i])
            # one commit per picture: SQLite holds the write lock as briefly as it can
            db.commit()
            live.append(g)
        done = sum(1 for _, g in gens if g.status in FINISHED)
        ended = sum(1 for _, g in gens if not _waiting(g) and g not in live)
        r.update(folder_id=(job.payload or {}).get("folder_id"), done=done, failed=ended - done)
        if not live and not any(_waiting(g) for _, g in gens):
            r["waiting_on"] = []
            job.result = dict(r)
            ctx.progress(1.0, f"{done} of {n} pictures")
            return dict(r)
        # waiting on the oldest only: it wakes us (autopilot.wake_parent) while the next is still on the GPU,
        # so the queue is topped up before it runs dry
        r["waiting_on"] = [live[0].job_id]
        job.result = dict(r)
        ctx.progress(0.05 + 0.95 * ended / max(1, n), f"{ended} of {n} pictures")
        oldest = live[0].job_id
        if not _other_work(ctx):
            # nothing else for this worker: watch here rather than spin through the queue
            deadline = time.monotonic() + POLL_S
            freed = False
            while time.monotonic() < deadline and not freed:
                time.sleep(1.0)
                ctx.progress(job.progress)
                freed = db.scalar(select(Job.status).where(Job.id == oldest)) not in LIVE_JOB
                if _other_work(ctx):
                    break
            if freed:
                continue
        db.commit()
        raise _w().JobDeferred(f"{ended} of {n} pictures", priority=WAIT_PRIORITY)


def cancel(db: Session, job: Job) -> None:
    """API cancel of a set: what is queued goes, running pictures are flagged, waiting tiles stop."""
    now = utcnow()
    for _, g in _gens(db, job.payload):
        child = _child(db, g)
        if child is None:
            if g.status == "queued":
                g.status = "failed"
            continue
        if child.status not in LIVE_JOB:
            continue
        running = child.status == "running"
        child.status = "cancelled"
        child.message = "Cancelling…" if running else "Cancelled"
        if not running:
            child.finished_at = now
            if g.status in ("queued", "generating"):
                g.status = "failed"
