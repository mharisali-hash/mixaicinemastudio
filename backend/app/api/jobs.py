from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import visibility as vis
from app.db import get_db
from app.implant.guard import private_ids
from app.models import Generation, Job, utcnow
from app.schemas import JobOut
from app.security import CurrentUser, get_current_user, require_editor
from app.services import get_owned, job_out

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(
    status: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    cur: CurrentUser = Depends(get_current_user),
):
    q = select(Job).where(Job.workspace_id == cur.workspace_id, *vis.where(Job, vis.wanted(cur)),
                          or_(Job.generation_id.is_(None), Job.generation_id.not_in(private_ids())))
    if status:
        q = q.where(Job.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if project_id:
        q = q.where(Job.project_id == project_id)
    q = q.order_by(Job.created_at.desc()).limit(100)
    return [job_out(j) for j in db.scalars(q).all()]


@router.get("/{job_id}", response_model=JobOut, summary="Get one job",
            description="Poll this after any 202 until `status` is `done` (or `failed` / `cancelled`). "
                        "`progress` runs 0 to 1 and `message` says what the worker is doing.")
def get_job(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return job_out(get_owned(db, Job, job_id, cur.workspace_id, "Job"))


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    job = get_owned(db, Job, job_id, cur.workspace_id, "Job")
    if job.status not in ("queued", "running"):
        raise HTTPException(409, f"Job is already {job.status}")
    was_running = job.status == "running"
    job.status = "cancelled"
    job.message = "Cancelling…" if was_running else "Cancelled"
    if not was_running:
        job.finished_at = utcnow()
        # a running job's generation is closed out by the worker when it notices
        if job.generation_id:
            g = db.get(Generation, job.generation_id)
            if g and g.status in ("queued", "generating"):
                g.status = "failed"
        if job.type == "generate":
            from app import image_batch

            # the pictures that would have come out of the same run go with it
            image_batch.on_job_finished(db, job)
    # the AI video podcast and Poetry Shorts wait on their children the same way
    if job.type in ("autopilot", "video_podcast", "poetry_short", "ad_campaign"):
        from app.autopilot import cancel_children

        cancel_children(db, job)
    elif job.type == "edit_set":
        from app import edit_set

        edit_set.cancel(db, job)
    db.commit()
    return job_out(job)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    job = get_owned(db, Job, job_id, cur.workspace_id, "Job")
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    job.status = "queued"
    job.progress = 0.0
    job.error = None
    job.message = "Waiting for a worker"
    job.attempts = 0
    job.started_at = job.finished_at = job.heartbeat_at = None
    job.gpu_seconds = None
    if job.type in ("autopilot", "video_podcast", "poetry_short", "ad_campaign", "edit_set"):
        from app.autopilot import on_retry  # resumes at the failed stage; finished stages stay done

        on_retry(job)
    if job.generation_id:
        g = db.get(Generation, job.generation_id)
        if g and g.status == "failed":
            g.status = "queued"
    db.commit()
    return job_out(job)
