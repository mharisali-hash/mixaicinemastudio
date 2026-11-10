"""Image → Create on the fast models: up to 8 pictures drawn in one ComfyUI run instead of one run each.

Every picture is still its own Library item with its own generation and its own content check. Only the first
gets a job; the others wait with no job (like the tiles of an Image Edit set) and are filled from the same run.
They all share the first one's seed and record their place in the batch (params.batch.index), and the driver
draws each picture's noise by that index (workflows.batch_latent), so one of them can be made again on its
own later and come out the same.

If the batch doesn't fit on the card, the rest go back to the queue as ordinary one-picture jobs.
"""
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Generation, Job, MediaItem, utcnow

log = logging.getLogger("mixai.image_batch")


def size_for(model, speed, count: int, params: dict) -> int:
    # a brand kit's output pass hangs off each picture's own job (brand.on_job_finished)
    if count < 2 or params.get("reference_ids") or params.get("img2img_of") or params.get("brand"):
        return 1
    if speed is not None and speed.template:
        # a speed with a graph of its own is the model's heavy variant (Krea 2 Raw): one at a time
        return 1
    return max(1, min(count, model.max_batch))


def wait_behind(db: Session, lead: Generation, item: MediaItem, index: int) -> Generation:
    """A picture of lead's batch: same prompt, params and seed, no job of its own."""
    g = Generation(workspace_id=lead.workspace_id, project_id=None, target_type="media", target_id=item.id,
                   kind=lead.kind, version=1, status="queued", prompt=lead.prompt,
                   params={**(lead.params or {}), "batch": {"lead": lead.id, "index": index}}, seed=lead.seed)
    db.add(g)
    db.flush()
    item.generation_id = g.id
    return g


def link(lead: Generation, rest: list[Generation]) -> None:
    lead.params = {**(lead.params or {}), "batch": {"ids": [lead.id, *(g.id for g in rest)], "index": 0}}


def index_of(g: Generation) -> int:
    return int(((g.params or {}).get("batch") or {}).get("index") or 0)


def _waiting(g: Generation | None, lead: Generation) -> bool:
    return (g is not None and g.job_id is None and g.status == "queued"
            and ((g.params or {}).get("batch") or {}).get("lead") == lead.id)


def followers(db: Session, lead: Generation) -> list[Generation | None]:
    """Pictures 2..n of the batch, None where one was deleted or has been handed a job of its own."""
    ids = ((lead.params or {}).get("batch") or {}).get("ids") or []
    out = [g if _waiting(g := db.get(Generation, gid), lead) else None for gid in ids[1:]]
    while out and out[-1] is None:
        out.pop()  # nothing to draw past the last picture still wanted
    return out


def prepare(db: Session, gen: Generation, params: dict, out_path) -> list[tuple[int, Generation, Path, str]]:
    """Fills the driver params for this generation's place in a batch; returns the waiting pictures it draws
    too, with their output paths."""
    if index_of(gen):
        params["batch_index"] = index_of(gen)
    rest = followers(db, gen)
    if not rest:
        return []
    paths = [out_path(g, ".png") if g is not None else None for g in rest]
    params["batch_out"] = [str(p[0]) if p else None for p in paths]
    return [(i, g, *p) for i, (g, p) in enumerate(zip(rest, paths), 1) if g is not None]


def _job_for(db: Session, g: Generation, lead_job: Job, **values) -> Job:
    job = Job(workspace_id=g.workspace_id, type="generate", generation_id=g.id, user_id=lead_job.user_id,
              payload={"generation_id": g.id}, **values)
    db.add(job)
    db.flush()
    g.job_id = job.id
    return job


def split(db: Session, lead: Generation, lead_job: Job) -> int:
    """Every picture still waiting on lead gets an ordinary job of its own; it keeps its index and seed, so it
    is the picture the batch would have drawn."""
    n = 0
    for g in followers(db, lead):
        if g is not None:
            _job_for(db, g, lead_job, message="Waiting for a worker")
            n += 1
    return n


def close(db: Session, lead: Generation, lead_job: Job) -> None:
    """The batch's job failed or was cancelled: the waiting pictures end the same way, each with a finished job
    of its own so its tile says why and Retry works on it alone."""
    status = "cancelled" if lead_job.status == "cancelled" else "failed"
    for g in followers(db, lead):
        if g is None:
            continue
        _job_for(db, g, lead_job, status=status, error=lead_job.error if status == "failed" else None,
                 message=lead_job.message or status.capitalize(), finished_at=utcnow())
        g.status = "failed"


def _end(db: Session, g: Generation, lead_job: Job, why: str) -> None:
    _job_for(db, g, lead_job, status="failed", error=why, message="Failed", finished_at=utcnow())
    g.status = "failed"


def run(ctx, gen: Generation, batch: list, params: dict, draw):
    """draw(params) -> DriverResult. Returns (result, the pictures it actually drew)."""
    from app import gpu_memory

    try:
        return draw(params), batch
    except Exception as e:
        if not gpu_memory.is_oom(str(e)):
            raise
        log.warning("batch of %d for %s ran out of GPU memory; queuing the rest one by one", len(batch) + 1, gen.id)
    n = split(ctx.db, gen, ctx.job)
    ctx.db.commit()
    ctx.progress(0.0, f"Making these one at a time; {n} more queued")
    return draw({k: v for k, v in params.items() if k != "batch_out"}), []


def finish(ctx, lead: Generation, batch: list, result) -> None:
    """After the run: each waiting picture gets its file, the lead's final prompt and its own content check."""
    from app import content_policy

    db, job = ctx.db, ctx.job
    shared = {k: v for k, v in (lead.params or {}).items() if k not in ("batch", "comfy")}
    record = (lead.params or {}).get("comfy")
    share = None
    if record and record.get("gpu_seconds"):
        # the job bills the whole run once; each picture's record shows its part of it
        share = round(float(record["gpu_seconds"]) / (len(batch) + 1), 3)
        lead.params = {**lead.params, "comfy": {**record, "gpu_seconds": share}}
    for i, g, abs_path, rel_path in batch:
        g.prompt = lead.prompt
        g.params = {**shared, "batch": (g.params or {}).get("batch")}
        if record:
            g.params["comfy"] = {**record, "inputs": {**(record.get("inputs") or {}), "batch_index": i},
                                 **({"gpu_seconds": share} if share else {})}
        if not abs_path.is_file():
            _end(db, g, job, "This picture didn't come back from the batch")
            continue
        try:
            content_policy.check_after(ctx, g, abs_path, result.media_type)
        except RuntimeError as e:
            _end(db, g, job, str(e))
            continue
        g.file_path, g.media_type, g.status = rel_path, result.media_type, "ready"
    # kept even if the lead's own picture is refused next
    db.commit()


def on_job_finished(db: Session, job: Job) -> None:
    """Worker hook (also run by the cancel route): the batch's other pictures follow its job."""
    lead = db.get(Generation, job.generation_id) if job.generation_id else None
    if lead is None or not ((lead.params or {}).get("batch") or {}).get("ids"):
        return
    if job.status in ("failed", "cancelled"):
        close(db, lead, job)
        return
    if job.status != "done":
        return
    from app import library as lib
    from app import thumbs

    for gid in lead.params["batch"]["ids"][1:]:
        g = db.get(Generation, gid)
        if g is None or g.job_id is not None or g.status != "ready":
            continue
        lib.on_generation_finished(db, g)
        try:
            thumbs.for_generation(g)
        except Exception:
            log.exception("couldn't make thumbs for %s", g.id)
