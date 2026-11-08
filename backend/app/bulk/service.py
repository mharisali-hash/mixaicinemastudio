"""Bulk batches: the hidden workspace, uploads, queueing one picture, pause / cancel / retry, deleting.

Everything a batch makes lives in one workspace nobody is a member of. The Library, the job tray and the
event stream are all per workspace, so thousands of dataset pictures never show up in anyone's studio, the
admin's included, and no listing needed a new filter.
"""
import csv
import io
import logging
import shutil
import uuid
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import content_policy as cp
from app import library as lib
from app import models_catalog as mc
from app import prompt_enhance, uploads
from app.config import get_settings
from app.models import BulkBatch, BulkItem, Generation, Job, MediaItem, Workspace, new_id, utcnow
from app.services import enqueue_generation

log = logging.getLogger("mixai.bulk")

ACTIONS = ("cutout", "edit")
RUN_JOB = "bulk_run"
EXPORT_JOB = "bulk_export"
# below everything else in the queue, the autopilot's own waits (-20) included: any request made while a
# batch runs, from the app, MCP or the REST API, the admin's own too, is claimed before the next picture
BULK_PRIORITY = -30
# the orchestrator waits below its own children, so a worker that serves every lane runs them first
BULK_WAIT = -40
SAMPLE_PRIORITY = 0  # the admin is sitting there waiting for the three previews
LIVE = ("queued", "running")
_WS_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://jafat.ai/bulk-datasets"))


def workspace(db: Session) -> str:
    if db.get(Workspace, _WS_ID) is None:
        db.add(Workspace(id=_WS_ID, name="Bulk datasets", owner_user_id=None))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()  # another upload made it first
    return _WS_ID


def get_batch(db: Session, batch_id: str) -> BulkBatch:
    b = db.get(BulkBatch, batch_id)
    if b is None:
        raise HTTPException(404, "Batch not found")
    return b


def counts(db: Session, batch_id: str) -> dict[str, int]:
    rows = db.execute(select(BulkItem.status, func.count()).where(BulkItem.batch_id == batch_id)
                      .group_by(BulkItem.status)).all()
    out = {s: 0 for s in ("uploaded", "queued", "running", "done", "failed", "cancelled")}
    out.update({s: n for s, n in rows})
    out["total"] = sum(n for _, n in rows)
    return out


# ---------------------------------------------------------------- options

def clean_options(action: str, raw: dict, *, admin_text_check=True) -> dict:
    opts: dict = {}
    if action == "edit":
        text = " ".join(str(raw.get("instruction") or "").split())[:1200]
        opts["instruction"] = text
        opts["magic_prompt"] = raw.get("magic_prompt") if raw.get("magic_prompt") in ("off", "auto", "on") else "off"
        aspect = raw.get("aspect") or ""
        if aspect and aspect not in lib.ASPECTS:
            raise HTTPException(422, f"Unknown shape '{aspect}'")
        opts["aspect"] = aspect
        if raw.get("model"):
            mc.get(raw["model"], "edit")  # 422 for an unknown or switched-off model
            opts["model"] = raw["model"]
        if raw.get("seed") is not None:
            opts["seed"] = int(raw["seed"]) % 2**31
        if text and admin_text_check:
            screen(text)
    else:
        opts["feather"] = round(max(0.0, min(20.0, float(raw.get("feather", 0.6) or 0))), 2)
    opts["keywords"] = " ".join(str(raw.get("keywords") or "").split())[:500]
    return opts


def screen(text: str) -> None:
    """Admins may build datasets the sexual-words rule would stop, never pictures of revered figures. The
    worker checks every prompt again; this only catches a bad instruction before thousands of jobs queue."""
    if cp.revered_words(text) and cp.prompt_refusal(text, admin=True):
        raise HTTPException(422, cp.RELIGIOUS_MSG)


def instruction_for(batch: BulkBatch, item: BulkItem) -> str:
    return (item.instruction or (batch.options or {}).get("instruction") or "").strip()


def keywords_for(batch: BulkBatch, item: BulkItem) -> str:
    return (item.keywords if item.keywords is not None else (batch.options or {}).get("keywords") or "").strip()


# ---------------------------------------------------------------- uploads

def _images_only(media_type: str) -> int:
    return uploads.limit_for(media_type) if media_type.startswith("image/") else 0


def _too_big(media_type: str | None) -> HTTPException:
    if media_type and not media_type.startswith("image/"):
        return HTTPException(415, "Bulk takes pictures only")
    return uploads._too_big(media_type)


IMAGES = uploads.Profile(uploads.sniff, _images_only, _too_big,
                         "PNG, JPEG or WebP pictures up to 40 MB, or RAW, HEIC or TIFF photos up to 250 MB",
                         uploads.PHOTO_MAX)


def clean_name(raw: str) -> str:
    """A folder upload's relative path, kept so the export can mirror the admin's folders; never '..'."""
    parts = [p for p in PurePosixPath((raw or "").replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    return "/".join(parts)[-500:] or "picture"


def check_disk(extra_bytes: int = 0) -> None:
    s = get_settings()
    free = shutil.disk_usage(s.data_dir if s.data_dir.exists() else s.data_dir.parent).free
    if free - extra_bytes < s.bulk_disk_reserve_gb * 1024**3:
        raise HTTPException(507, f"The server has {free / 1024**3:.0f} GB free; bulk uploads stop at "
                                 f"{s.bulk_disk_reserve_gb} GB so the studio keeps working. Export and delete "
                                 "an old batch, or free some space.")


def arrived(db: Session, batch_id: str, files: list[tuple[str, int]]) -> list[str]:
    """Which of these (name, size) pairs are already in the batch: a reselected folder resumes from here."""
    names = {clean_name(n) for n, _ in files}
    have = set(db.execute(select(BulkItem.filename, BulkItem.size)
                          .where(BulkItem.batch_id == batch_id, BulkItem.filename.in_(names))).all())
    return [n for n, size in files if (clean_name(n), size) in have]


def find_item(db: Session, batch_id: str, name: str, size: int) -> BulkItem | None:
    return db.scalars(select(BulkItem).where(BulkItem.batch_id == batch_id, BulkItem.filename == name,
                                             BulkItem.size == size)).first()


def add_upload(db: Session, batch: BulkBatch, user_id: str, got: uploads.Received, name: str) -> tuple[BulkItem, bool]:
    """(item, was it already there). Runs in a threadpool: the importer probes, converts and makes thumbs."""
    from app.catalogue import importer

    have = find_item(db, batch.id, name, got.size)
    if have is not None:
        got.path.unlink(missing_ok=True)
        return have, True
    folder, rel = lib.media_dir(batch.workspace_id)
    got.filename = PurePosixPath(name).name
    media, _ = importer.store(db, batch.workspace_id, user_id, got, folder, rel)
    seq = (db.scalar(select(func.max(BulkItem.seq)).where(BulkItem.batch_id == batch.id)) or 0) + 1
    item = BulkItem(batch_id=batch.id, seq=seq, filename=name, size=got.size, sha256=got.sha256,
                    media_id=media.id, source_gen_id=media.generation_id, status="uploaded")
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        # the same file sent twice at once (a retried request): keep the first
        db.rollback()
        lib.delete_item(db, db.get(MediaItem, media.id))
        db.commit()
        return find_item(db, batch.id, name, got.size), True
    return item, False


def apply_prompts(db: Session, batch: BulkBatch, data: bytes) -> dict:
    """A CSV (filename, instruction, keywords) for per-picture prompts. Names match the uploaded path, or just
    the file name when that is unique in the batch."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Save the CSV as UTF-8") from None
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise HTTPException(422, "The CSV has no rows (the first line should be: filename,instruction,keywords)")
    cols = {c.strip().lower(): c for c in rows[0].keys() if c}
    fcol = cols.get("filename") or cols.get("file") or cols.get("name")
    icol = cols.get("instruction") or cols.get("prompt") or cols.get("caption")
    kcol = cols.get("keywords") or cols.get("tags")
    if not fcol or not (icol or kcol):
        raise HTTPException(422, "The CSV needs a filename column and an instruction or keywords column")
    items = db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id)).all()
    by_path = {i.filename.lower(): i for i in items}
    by_base: dict[str, list[BulkItem]] = {}
    for i in items:
        by_base.setdefault(PurePosixPath(i.filename).name.lower(), []).append(i)
    matched, unmatched, checked = 0, [], set()
    for row in rows:
        key = clean_name(row.get(fcol) or "").lower()
        item = by_path.get(key)
        if item is None and len(by_base.get(PurePosixPath(key).name, [])) == 1:
            item = by_base[PurePosixPath(key).name][0]
        if item is None:
            unmatched.append(row.get(fcol) or "")
            continue
        if icol:
            instr = " ".join((row.get(icol) or "").split())[:1200] or None
            if instr and instr not in checked:
                screen(instr)
                checked.add(instr)
            item.instruction = instr
        if kcol:
            item.keywords = " ".join((row.get(kcol) or "").split())[:500] or None
        matched += 1
    db.commit()
    return {"rows": len(rows), "matched": matched, "unmatched": unmatched[:50], "unmatched_count": len(unmatched)}


# ---------------------------------------------------------------- queueing one picture

def _edit_job(db: Session, batch: BulkBatch, item: BulkItem, source: Generation) -> Job:
    opts = batch.options or {}
    text = instruction_for(batch, item)
    if not text:
        raise ValueError("No instruction for this picture")
    model, chosen = mc.choose(opts.get("model") or None, "edit", text, refs=1)
    if opts.get("aspect"):
        w, h = lib.size_for_aspect(opts["aspect"])
    else:
        sw, sh = lib._image_dims(source)
        w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
    params = {"width": w, "height": h, "aspect": opts.get("aspect") or None, "instruction": text,
              "edit_of": [{"media_id": item.media_id, "generation_id": source.id}], "reference_ids": [source.id],
              "model": model.id, "created_by": {"user_id": batch.user_id, "flow": "bulk"},
              "magic_prompt": prompt_enhance.marker(opts.get("magic_prompt") or "off", False, "edit"),
              "user_prompt": text, "bulk": {"batch_id": batch.id, "item_id": item.id}, **chosen}
    out = MediaItem(id=new_id(), workspace_id=batch.workspace_id, kind="image", origin="generated",
                    title=PurePosixPath(item.filename).stem[:300], tags=[], width=w, height=h)
    db.add(out)
    db.flush()
    g = enqueue_generation(db, workspace_id=batch.workspace_id, project_id=None, target_type="media",
                           target_id=out.id, kind="image", prompt=text, params=params, seed=opts.get("seed"))
    out.generation_id = g.id
    return db.get(Job, g.job_id)


def queue_item(db: Session, batch: BulkBatch, item: BulkItem, *, priority: int, parent_job_id: str | None = None) -> Job | None:
    """The item's generate job, built the same way Cutout and Image Edit build theirs. None (and the item
    marked failed) when the picture can't be queued at all."""
    from app.photo import restore

    source = db.get(Generation, item.source_gen_id or "")
    try:
        if source is None or not source.file_path:
            raise ValueError("The uploaded picture is missing")
        if batch.action == "cutout":
            edge = {"feather": (batch.options or {}).get("feather", 0.6)}
            job = restore.queue_tool(db, source, "cutout", {"edge": edge, "op": "replace"}, batch.user_id)
        else:
            job = _edit_job(db, batch, item, source)
    except (ValueError, HTTPException, restore.RestoreError) as e:
        item.status, item.error = "failed", getattr(e, "detail", None) or str(e)
        item.finished_at = utcnow()
        return None
    job.priority = priority
    job.user_id = batch.user_id
    job.payload = {**(job.payload or {}), "bulk_item_id": item.id, "bulk_batch_id": batch.id,
                   **({"parent_job_id": parent_job_id} if parent_job_id else {})}
    item.job_id = job.id
    item.status = "queued"
    item.error = None
    item.result_gen_id = None
    item.prompt_used = None
    item.finished_at = None
    item.attempts += 1
    return job


def sync_item(db: Session, item: BulkItem, job: Job) -> None:
    """Copy a finished child job onto its item."""
    if job.status in LIVE:
        item.status = job.status
        return
    item.finished_at = job.finished_at or utcnow()
    if job.status == "done":
        g = db.get(Generation, job.generation_id or "")
        item.status = "done"
        item.result_gen_id = g.id if g is not None else None
        item.prompt_used = (g.prompt if g is not None else None) or None
    elif job.status == "failed":
        item.status, item.error = "failed", (job.error or "Failed")[:2000]
    else:
        item.status = "cancelled"


def on_job_finished(db: Session, job: Job) -> None:
    """Worker hook: a bulk picture ended. Its item is updated and a waiting orchestrator tops the queue up."""
    item_id = (job.payload or {}).get("bulk_item_id")
    if not item_id:
        return
    item = db.get(BulkItem, item_id)
    if item is None or item.job_id != job.id:
        return
    sync_item(db, item, job)
    batch = db.get(BulkBatch, item.batch_id)
    run = db.get(Job, batch.run_job_id) if batch is not None and batch.run_job_id else None
    if run is not None and run.status == "queued" and run.priority == BULK_WAIT:
        run.priority = BULK_PRIORITY


# ---------------------------------------------------------------- run, pause, cancel

def live_run(db: Session, batch: BulkBatch) -> Job | None:
    j = db.get(Job, batch.run_job_id) if batch.run_job_id else None
    return j if j is not None and j.status in LIVE else None


def start(db: Session, batch: BulkBatch) -> Job:
    if not db.scalar(select(func.count()).select_from(BulkItem).where(BulkItem.batch_id == batch.id)):
        raise HTTPException(422, "Upload some pictures first")
    if batch.action == "edit" and not (batch.options or {}).get("instruction") and not db.scalar(
            select(func.count()).select_from(BulkItem).where(BulkItem.batch_id == batch.id,
                                                            BulkItem.instruction.is_not(None))):
        raise HTTPException(422, "Write the instruction first")
    batch.status = "running"
    job = live_run(db, batch)
    if job is not None:
        return job
    job = Job(workspace_id=batch.workspace_id, type=RUN_JOB, priority=BULK_PRIORITY, user_id=batch.user_id,
              payload={"batch_id": batch.id}, message="Waiting for a worker")
    db.add(job)
    db.flush()
    batch.run_job_id = job.id
    return job


def _unqueue(db: Session, batch: BulkBatch, *, to: str) -> int:
    """Take the batch's waiting pictures off the queue. A picture already rendering finishes (pause) or is
    stopped at its next progress tick (cancel)."""
    now = utcnow()
    n = 0
    items = db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id, BulkItem.status.in_(LIVE))).all()
    for item in items:
        job = db.get(Job, item.job_id or "")
        if job is None:
            item.status = to
            continue
        if job.status == "queued" or (to == "cancelled" and job.status == "running"):
            running = job.status == "running"
            job.status = "cancelled"
            job.message = "Cancelling…" if running else "Cancelled"
            if not running:
                job.finished_at = now
                g = db.get(Generation, job.generation_id or "")
                if g is not None and g.status in ("queued", "generating"):
                    g.status = "failed"
                # paused pictures go back to the start of the line, and the try doesn't count
                item.status = to
                if to == "uploaded":
                    item.attempts = max(0, item.attempts - 1)
                    item.job_id = None
            n += 1
    return n


def pause(db: Session, batch: BulkBatch) -> None:
    batch.status = "paused"
    run = live_run(db, batch)
    if run is not None and run.status == "queued":
        run.status, run.message, run.finished_at = "cancelled", "Paused", utcnow()
    _unqueue(db, batch, to="uploaded")


def cancel(db: Session, batch: BulkBatch) -> None:
    batch.status = "cancelled"
    run = live_run(db, batch)
    if run is not None:
        run.status, run.message = "cancelled", "Cancelled"
        if run.finished_at is None and run.started_at is None:
            run.finished_at = utcnow()
    _unqueue(db, batch, to="cancelled")


def retry_failed(db: Session, batch: BulkBatch) -> int:
    n = db.execute(update(BulkItem).where(BulkItem.batch_id == batch.id,
                                          BulkItem.status.in_(("failed", "cancelled")))
                   .values(status="uploaded", error=None, job_id=None)).rowcount
    return n or 0


def preview(db: Session, batch: BulkBatch, n: int = 3) -> list[BulkItem]:
    """Three pictures at normal priority so the admin sees the result before committing thousands."""
    if live_run(db, batch) is not None:
        raise HTTPException(409, "The batch is running; pause it to try new settings")
    picks = db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id, BulkItem.sample.is_(True))
                       .order_by(BulkItem.seq)).all()
    if len(picks) < n:
        picks += db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id, BulkItem.sample.is_(False),
                                                   BulkItem.status == "uploaded")
                            .order_by(BulkItem.seq).limit(n - len(picks))).all()
    if not picks:
        raise HTTPException(422, "Upload some pictures first")
    for item in picks:
        old = db.get(Job, item.job_id or "")
        if old is not None and old.status in LIVE:
            continue
        item.sample = True
        queue_item(db, batch, item, priority=SAMPLE_PRIORITY)
    return picks


def options_changed(db: Session, batch: BulkBatch) -> None:
    """New settings: the samples made with the old ones are redone by the next preview or run."""
    db.execute(update(BulkItem).where(BulkItem.batch_id == batch.id, BulkItem.sample.is_(True),
                                      BulkItem.status.in_(("done", "failed", "cancelled")))
               .values(status="uploaded", result_gen_id=None, prompt_used=None, error=None, job_id=None))


# ---------------------------------------------------------------- delete

def delete(db: Session, batch: BulkBatch) -> int:
    cancel(db, batch)
    db.flush()
    ids = set()
    for item in db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id)).all():
        ids.add(item.media_id)
        if item.result_gen_id:
            g = db.get(Generation, item.result_gen_id)
            if g is not None and g.target_type == "media":
                ids.add(g.target_id)
    n = 0
    for mid in filter(None, ids):
        m = db.get(MediaItem, mid)
        if m is not None and m.workspace_id == batch.workspace_id:
            lib.delete_item(db, m)
            n += 1
    from app.bulk.export import remove_exports

    remove_exports(db, batch)
    db.delete(batch)
    return n


def item_file(db: Session, gen_id: str | None) -> Path | None:
    g = db.get(Generation, gen_id or "")
    if g is None or not g.file_path:
        return None
    p = get_settings().data_dir / g.file_path
    return p if p.is_file() else None
