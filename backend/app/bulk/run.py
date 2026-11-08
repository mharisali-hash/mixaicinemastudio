"""The bulk orchestrator: keeps a few of a batch's pictures on the image queue, never thousands.

Queueing a whole batch at once would work for priority alone (it sits at BULK_PRIORITY, below everyone), but
it would fill the job table with thousands of rows, make pause a mass update and give a fresh request
nothing to overtake mid-batch. With BULK_IN_FLIGHT children (default 2) the image GPU never waits for the
next picture, and anything anybody asks for meanwhile is at most one picture away.

Like the podcast and autopilot orchestrators it never blocks a worker: when a worker has other work it hands
itself back to the queue (JobDeferred at BULK_WAIT), and a finishing child lifts it again (on_job_finished).
"""
import time
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select

from app.bulk import service as bs
from app.config import get_settings
from app.models import BulkBatch, BulkItem, Job

POLL_S = 10.0


def _w():
    from app import worker

    return worker


def _other_work(ctx) -> bool:
    lanes = getattr(ctx, "lanes", None)
    lane_ok = [Job.lane.in_(lanes)] if lanes else []
    return bool(ctx.db.scalar(select(func.count()).select_from(Job).where(
        Job.status == "queued", Job.priority > bs.BULK_WAIT, Job.id != ctx.job.id, *lane_ok)))


def _in_flight(db, batch_id: str) -> list[BulkItem]:
    return list(db.scalars(select(BulkItem).where(BulkItem.batch_id == batch_id, BulkItem.status.in_(bs.LIVE))))


def _resync(db, items: list[BulkItem]) -> None:
    # the finished hook does this as each picture ends; this catches one that died with its worker
    for item in items:
        job = db.get(Job, item.job_id or "")
        if job is None:
            item.status = "uploaded"
        elif job.status not in bs.LIVE:
            bs.sync_item(db, item, job)


def _top_up(ctx, batch: BulkBatch) -> int:
    db = ctx.db
    live = _in_flight(db, batch.id)
    want = max(1, get_settings().bulk_in_flight) - len(live)
    added = 0
    while want > 0:
        nxt = db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id, BulkItem.status == "uploaded")
                         .order_by(BulkItem.seq).limit(1)).first()
        if nxt is None:
            break
        job = bs.queue_item(db, batch, nxt, priority=bs.BULK_PRIORITY, parent_job_id=ctx.job.id)
        # one commit per picture: SQLite holds the write lock for as short a time as it can
        db.commit()
        if job is not None:
            want -= 1
            added += 1
    return added


def _tick(ctx, batch: BulkBatch) -> None:
    c = bs.counts(ctx.db, batch.id)
    ended = c["done"] + c["failed"] + c["cancelled"]
    ctx.job.result = {"batch_id": batch.id, "counts": c,
                      "waiting_on": [i.job_id for i in _in_flight(ctx.db, batch.id) if i.job_id]}
    ctx.progress(ended / max(1, c["total"]), f"{ended} of {c['total']} pictures")


def handle(ctx) -> dict:
    db = ctx.db
    batch = db.get(BulkBatch, (ctx.job.payload or {}).get("batch_id") or "")
    if batch is None:
        raise RuntimeError("The batch has been deleted")
    while True:
        db.refresh(batch)
        if batch.status != "running":
            # paused or cancelled from the page: what is queued was already taken off
            return {"batch_id": batch.id, "stopped": batch.status, "counts": bs.counts(db, batch.id)}
        _resync(db, _in_flight(db, batch.id))
        _top_up(ctx, batch)
        live = _in_flight(db, batch.id)
        if not live:
            batch.status = "done"
            _tick(ctx, batch)
            db.commit()
            return {"batch_id": batch.id, "counts": bs.counts(db, batch.id)}
        _tick(ctx, batch)
        if not _other_work(ctx):
            # nothing else for this worker to do: watch here rather than spin through the queue
            deadline = time.monotonic() + POLL_S
            while time.monotonic() < deadline:
                time.sleep(1.0)
                ctx.progress(ctx.job.progress)
                if len(_in_flight(db, batch.id)) < len(live) or _other_work(ctx):
                    break
            if len(_in_flight(db, batch.id)) < len(live):
                continue
        db.commit()
        raise _w().JobDeferred(f"Bulk: {(ctx.job.result or {}).get('counts', {}).get('done', 0)} done",
                               priority=bs.BULK_WAIT)


# ---------------------------------------------------------------- ZIP uploads

UNZIP_JOB = "bulk_unzip"
PICTURE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".dng", ".cr2",
               ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}


def handle_unzip(ctx) -> dict:
    """Unpack an uploaded ZIP into the batch, one picture at a time through the same path a folder upload
    takes. Re-running it after a restart skips what already arrived."""
    from app import uploads
    from fastapi import HTTPException

    db, job = ctx.db, ctx.job
    batch = db.get(BulkBatch, (job.payload or {}).get("batch_id") or "")
    src = get_settings().data_dir / (job.payload or {}).get("zip", "")
    if batch is None or not src.is_file():
        raise RuntimeError("The uploaded ZIP is gone; upload it again")
    added, skipped, bad = 0, 0, []
    with zipfile.ZipFile(src) as z:
        entries = [i for i in z.infolist() if not i.is_dir() and PurePosixPath(i.filename).suffix.lower() in PICTURE_EXT
                   and not PurePosixPath(i.filename).name.startswith((".", "__MACOSX"))
                   and "__MACOSX" not in i.filename]
        total = sum(i.file_size for i in entries)
        # what the ZIP says it unpacks to must fit, so a zip bomb stops before anything is written
        bs.check_disk(total)
        folder, _ = bs.lib.media_dir(batch.workspace_id)
        folder.mkdir(parents=True, exist_ok=True)
        for n, info in enumerate(entries):
            name = bs.clean_name(info.filename)
            if bs.find_item(db, batch.id, name, info.file_size) is not None:
                skipped += 1
                continue
            if info.file_size > uploads.PHOTO_MAX:
                bad.append({"file": name, "error": "Too big"})
                continue
            tmp = folder / f".upload-{job.id}-{n}.part"
            try:
                with z.open(info) as fin, open(tmp, "wb") as fout:
                    head = fin.read(uploads.SNIFF_BYTES)
                    fout.write(head)
                    while chunk := fin.read(1024 * 1024):
                        fout.write(chunk)
                media_type = uploads.sniff(head, name)
                if not media_type or not media_type.startswith("image/"):
                    raise HTTPException(415, "Not a picture")
                got = uploads.Received(tmp, media_type, info.file_size, PurePosixPath(name).name,
                                       sha256=_sha(tmp))
                bs.add_upload(db, batch, batch.user_id or "", got, name)
                added += 1
            except HTTPException as e:
                db.rollback()
                bad.append({"file": name, "error": str(e.detail)})
            finally:
                tmp.unlink(missing_ok=True)
            if n % 10 == 0:
                ctx.progress((n + 1) / max(1, len(entries)), f"Unpacked {n + 1} of {len(entries)}")
    src.unlink(missing_ok=True)
    return {"added": added, "skipped": skipped, "failed": bad[:200], "failed_count": len(bad)}


def _sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()
