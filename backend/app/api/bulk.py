"""Admin bulk runs: /api/admin/bulk. Admins only (404 for everyone else), app sessions only."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, thumbs, uploads
from app.api.media import resolve_media_path
from app.bulk import export as bx, run as brun, service as bs
from app.config import get_settings
from app.db import get_db, release
from app.models import BulkBatch, BulkItem, Generation, Job
from app.security import CurrentUser, require_admin, session_only

router = APIRouter(prefix="/admin/bulk", tags=["admin"])


class BatchIn(BaseModel):
    name: str = Field("", max_length=200)
    action: Literal["cutout", "edit", "remove"]
    options: dict = Field(default_factory=dict)


class BatchPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    options: dict | None = None


class FileRef(BaseModel):
    name: str = Field(max_length=1000)
    size: int


class CheckIn(BaseModel):
    files: list[FileRef] = Field(max_length=20000)


class ExportIn(BaseModel):
    originals: bool = False
    captions: bool = True


class BatchOut(BaseModel):
    id: str
    name: str
    action: str
    options: dict
    status: str
    counts: dict
    run_job_id: str | None
    eta_s: float | None = None
    created_at: datetime
    updated_at: datetime


def _eta(db: Session, b: BulkBatch, c: dict) -> float | None:
    # the pace of the last twenty pictures: what else the GPU is doing changes it all the time
    if b.status != "running":
        return None
    stamps = list(db.scalars(select(BulkItem.finished_at).where(BulkItem.batch_id == b.id, BulkItem.status == "done",
                                                               BulkItem.finished_at.is_not(None))
                             .order_by(BulkItem.finished_at.desc()).limit(21)))
    if len(stamps) < 5:
        return None
    span = (stamps[0] - stamps[-1]).total_seconds()
    if span <= 0:
        return None
    return round((c["uploaded"] + c["queued"] + c["running"]) * span / (len(stamps) - 1))


def _out(db: Session, b: BulkBatch) -> BatchOut:
    c = bs.counts(db, b.id)
    return BatchOut(id=b.id, name=b.name, action=b.action, options=b.options or {}, status=b.status, counts=c,
                    run_job_id=b.run_job_id, eta_s=_eta(db, b, c), created_at=b.created_at, updated_at=b.updated_at)


def _thumb(gen_id: str | None, item_id: str, batch_id: str, which: str) -> str | None:
    return f"/api/admin/bulk/{batch_id}/items/{item_id}/{which}" if gen_id else None


@router.get("", response_model=list[BatchOut])
@session_only
def list_batches(db: Session = Depends(get_db), _=Depends(require_admin)):
    rows = db.scalars(select(BulkBatch).order_by(BulkBatch.created_at.desc()).limit(100)).all()
    return [_out(db, b) for b in rows]


@router.post("", response_model=BatchOut, status_code=201)
@session_only
def create_batch(body: BatchIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    name = " ".join(body.name.split()) or f"Batch {datetime.now():%d %b %H:%M}"
    b = BulkBatch(workspace_id=bs.workspace(db), user_id=cur.id, name=name, action=body.action,
                  options=bs.clean_options(body.action, body.options), status="uploading", exports=[])
    db.add(b)
    db.flush()
    audit.record(db, cur.user, "bulk_create", "bulk_batch", b.id, name=name, kind=body.action)
    db.commit()
    return _out(db, b)


@router.get("/{batch_id}", response_model=BatchOut)
@session_only
def get_batch(batch_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    return _out(db, bs.get_batch(db, batch_id))


@router.patch("/{batch_id}", response_model=BatchOut)
@session_only
def patch_batch(batch_id: str, body: BatchPatch, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    if body.name is not None and body.name.strip():
        b.name = " ".join(body.name.split())
    if body.options is not None:
        if bs.live_run(db, b) is not None:
            raise HTTPException(409, "Pause the batch before changing its settings")
        new = bs.clean_options(b.action, {**(b.options or {}), **body.options})
        if new != (b.options or {}):
            b.options = new
            bs.options_changed(db, b)
            audit.record(db, cur.user, "bulk_options", "bulk_batch", b.id, options=new)
    db.commit()
    return _out(db, b)


@router.delete("/{batch_id}", status_code=204)
@session_only
def delete_batch(batch_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    n = bs.delete(db, b)
    audit.record(db, cur.user, "bulk_delete", "bulk_batch", batch_id, name=b.name, pictures=n)
    db.commit()


# ---------------------------------------------------------------- uploads

@router.post("/{batch_id}/check")
@session_only
def check_files(batch_id: str, body: CheckIn, db: Session = Depends(get_db), _=Depends(require_admin)):
    """Which of the picked files are already in: the page skips them, which is how a dropped upload resumes."""
    bs.get_batch(db, batch_id)
    have = bs.arrived(db, batch_id, [(f.name, f.size) for f in body.files])
    return {"have": have}


@router.post("/{batch_id}/files", status_code=201)
@session_only
async def upload_file(batch_id: str, request: Request, name: str = Query("", max_length=1000),
                      db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    if b.status in ("cancelled",):
        raise HTTPException(409, "This batch was cancelled")
    bs.check_disk(int(request.headers.get("content-length") or 0))
    folder, _ = bs.lib.media_dir(b.workspace_id)
    got = await uploads.receive(request, folder, bs.IMAGES)
    try:
        item, dup = await run_in_threadpool(bs.add_upload, db, b, cur.id, got, bs.clean_name(name or got.filename))
    except BaseException:
        got.path.unlink(missing_ok=True)
        raise
    return {"id": item.id, "filename": item.filename, "duplicate": dup}


ZIP_PROFILE = uploads.Profile(
    lambda head: "application/zip" if head.startswith(b"PK\x03\x04") else None,
    lambda _t: get_settings().bulk_zip_max_gb * 1024**3,
    lambda _t: HTTPException(413, f"A bulk ZIP can be at most {get_settings().bulk_zip_max_gb} GB; "
                                  "upload the folder instead, it has no limit and resumes"),
    "a .zip of pictures", 64 * 1024**3)


@router.post("/{batch_id}/zip", status_code=202)
@session_only
async def upload_zip(batch_id: str, request: Request, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    size = int(request.headers.get("content-length") or 0)
    if size > get_settings().bulk_zip_max_gb * 1024**3:
        raise ZIP_PROFILE.too_big(None)
    bs.check_disk(size * 2)
    folder = bx.exports_dir(b.workspace_id).parent / "bulk-zips"
    got = await uploads.receive(request, folder, ZIP_PROFILE)
    rel = got.path.relative_to(get_settings().data_dir).as_posix()
    job = Job(workspace_id=b.workspace_id, type=brun.UNZIP_JOB, priority=10, user_id=cur.id,
              payload={"batch_id": b.id, "zip": rel}, message="Waiting for a worker")
    db.add(job)
    audit.record(db, cur.user, "bulk_zip", "bulk_batch", b.id, bytes=got.size)
    db.commit()
    return {"job_id": job.id}


@router.post("/{batch_id}/prompts")
@session_only
async def upload_prompts(batch_id: str, request: Request, db: Session = Depends(get_db),
                         cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    form = await request.form()
    f = form.get("file")
    if f is None or not hasattr(f, "read"):
        raise HTTPException(422, "Send the CSV in a field named 'file'")
    data = await f.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "The prompts CSV can be at most 8 MB")
    out = await run_in_threadpool(bs.apply_prompts, db, b, data)
    bs.options_changed(db, b)
    audit.record(db, cur.user, "bulk_prompts", "bulk_batch", b.id, matched=out["matched"])
    db.commit()
    return out


# ---------------------------------------------------------------- running

@router.post("/{batch_id}/preview")
@session_only
def preview(batch_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    picks = bs.preview(db, b)
    db.commit()
    return {"items": [i.id for i in picks]}


@router.post("/{batch_id}/run", response_model=BatchOut)
@session_only
def run_batch(batch_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    job = bs.start(db, b)
    audit.record(db, cur.user, "bulk_run", "bulk_batch", b.id, job_id=job.id, pictures=bs.counts(db, b.id)["total"])
    db.commit()
    return _out(db, b)


@router.post("/{batch_id}/pause", response_model=BatchOut)
@session_only
def pause_batch(batch_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    if b.status == "running":
        bs.pause(db, b)
    db.commit()
    return _out(db, b)


@router.post("/{batch_id}/cancel", response_model=BatchOut)
@session_only
def cancel_batch(batch_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    bs.cancel(db, b)
    audit.record(db, cur.user, "bulk_cancel", "bulk_batch", b.id)
    db.commit()
    return _out(db, b)


@router.post("/{batch_id}/retry-failed", response_model=BatchOut)
@session_only
def retry_failed(batch_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    if bs.retry_failed(db, b) and b.status in ("done", "cancelled", "running"):
        bs.start(db, b)
    db.commit()
    return _out(db, b)


@router.get("/{batch_id}/items")
@session_only
def list_items(batch_id: str, status: str | None = None, after: int = 0, limit: int = Query(100, le=500),
               db: Session = Depends(get_db), _=Depends(require_admin)):
    bs.get_batch(db, batch_id)
    q = select(BulkItem).where(BulkItem.batch_id == batch_id, BulkItem.seq > after)
    if status == "samples":
        q = q.where(BulkItem.sample.is_(True))
    elif status:
        q = q.where(BulkItem.status.in_([s for s in status.split(",") if s]))
    rows = db.scalars(q.order_by(BulkItem.seq).limit(limit)).all()
    out = [{"id": i.id, "seq": i.seq, "filename": i.filename, "status": i.status, "error": i.error,
            "instruction": i.instruction, "keywords": i.keywords, "prompt_used": i.prompt_used, "sample": i.sample,
            "source_url": _thumb(i.source_gen_id, i.id, batch_id, "source"),
            "result_url": _thumb(i.result_gen_id, i.id, batch_id, "result") if i.status == "done" else None}
           for i in rows]
    return {"items": out, "next": rows[-1].seq if len(rows) == limit else None}


@router.get("/{batch_id}/items/{item_id}/{which}")
@session_only
def item_picture(batch_id: str, item_id: str, which: Literal["source", "result"], w: int = Query(256),
                 db: Session = Depends(get_db), _=Depends(require_admin)):
    item = db.get(BulkItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(404, "Not found")
    b = db.get(BulkBatch, batch_id)
    g = db.get(Generation, (item.source_gen_id if which == "source" else item.result_gen_id) or "")
    data_dir = get_settings().data_dir
    src = resolve_media_path(g.file_path, b.workspace_id, data_dir) if g is not None and g.file_path else None
    if src is None:
        raise HTTPException(404, "No picture yet")
    release(db)
    dest = src if w == 0 else (thumbs.ensure(src, g.media_type, w) if w in thumbs.SIZES else None)
    if dest is None:
        raise HTTPException(422, f"w must be 0 (full size) or one of {', '.join(map(str, thumbs.SIZES))}")
    return FileResponse(dest, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- export

@router.post("/{batch_id}/export", status_code=202)
@session_only
def start_export(batch_id: str, body: ExportIn, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    if not db.scalar(select(func.count()).select_from(BulkItem).where(BulkItem.batch_id == b.id,
                                                                       BulkItem.status == "done")):
        raise HTTPException(422, "No finished pictures to export yet")
    job = bx.queue(db, b, originals=body.originals, captions=body.captions)
    audit.record(db, cur.user, "bulk_export", "bulk_batch", b.id, job_id=job.id, originals=body.originals)
    db.commit()
    return {"job_id": job.id}


@router.get("/{batch_id}/exports")
@session_only
def list_exports(batch_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    out = []
    for jid in reversed(b.exports or []):
        j = db.get(Job, jid)
        if j is None:
            continue
        parts = (j.result or {}).get("parts") or []
        out.append({"job_id": j.id, "status": j.status, "progress": j.progress, "message": j.message,
                    "error": j.error, "created_at": j.created_at, "options": {k: j.payload.get(k) for k in ("originals", "captions")},
                    "parts": [{"n": n, "count": p["count"], "bytes": p["bytes"],
                               "url": f"/api/admin/bulk/{b.id}/exports/{j.id}/{n}"} for n, p in enumerate(parts, 1)]})
    return {"exports": out}


@router.get("/{batch_id}/exports/{job_id}/{n}")
@session_only
def download_part(batch_id: str, job_id: str, n: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    b = bs.get_batch(db, batch_id)
    job = db.get(Job, job_id)
    if job is None or job.id not in (b.exports or []) or job.status != "done":
        raise HTTPException(404, "Export not found")
    path = bx.part_path(job, n)
    if path is None:
        raise HTTPException(410, "This part has been cleared; export again")
    total = len((job.result or {}).get("parts") or [])
    slug = "".join(c if c.isalnum() else "-" for c in b.name.lower()).strip("-")[:60] or "bulk"
    release(db)
    # FileResponse streams in chunks, so a 2 GB part never sits in memory
    return FileResponse(path, media_type="application/zip", filename=f"{slug}-part{n}-of-{total}.zip",
                        headers={"Cache-Control": "private, no-store"})
