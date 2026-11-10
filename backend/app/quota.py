"""GPU limits (contract v39): what a user may still start today, and the one check that refuses new work.

The check runs in before_flush for new top-level jobs made in a request (security.bind_session marks the
session), so the app, API tokens and MCP tools all hit it. The worker's sessions aren't marked: child jobs
of something already accepted always go through, and a running job is never stopped.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app import packages as pk
from app.lanes import AUDIO_KINDS, IMAGE_KINDS, MODEL_KINDS, VIDEO_KINDS
from app.models import Generation, Job, Package, Subscription, UsageLedger, User, utcnow

# Session.info keys set by security.bind_session
QUOTA_USER = "quota_user"
QUOTA_CHANNEL = "quota_channel"
# this transaction passed the minutes / queue check already: a batch of four counts as one request
_PASSED = "quota_passed"


class QuotaExceeded(HTTPException):
    """429 with the numbers the app shows (main.py turns it into a flat JSON body). Still an HTTPException so
    MCP tools report it like any other refusal."""

    def __init__(self, detail: str, *, reason: str, used_min: float, limit_min: float | None,
                 resets_at: datetime | None, queued: int | None = None, max_queued: int | None = None):
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS, detail)
        self.body = {"detail": detail, "reason": reason, "used_min": round(used_min, 1),
                     "limit_min": None if limit_min is None else round(limit_min, 1),
                     "resets_at": _iso(resets_at), "queued": queued, "max_queued": max_queued}


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() + "Z" if at else None


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def month_start(now: datetime) -> datetime:
    return day_start(now).replace(day=1)


def next_month(now: datetime) -> datetime:
    first = month_start(now)
    return (first + timedelta(days=32)).replace(day=1)


@dataclass
class Limits:
    unlimited: bool
    package: Package | None = None
    subscription: Subscription | None = None
    gpu_min_day: float | None = None
    gpu_min_month: float | None = None
    max_queued: int | None = None
    grant_min: float = 0.0
    models: list | None = None
    features: dict = field(default_factory=dict)

    @property
    def day_limit(self) -> float | None:
        return None if self.gpu_min_day is None else self.gpu_min_day + self.grant_min

    @property
    def month_limit(self) -> float | None:
        return None if self.gpu_min_month is None else self.gpu_min_month + self.grant_min

    def allows(self, feature: str) -> bool:
        return self.unlimited or self.features.get(feature, True) is not False


def limits_for(db: Session, user: User, now: datetime | None = None) -> Limits:
    """The active subscription's package (Free when there's none), plus live grants, with the user's override
    on top. Admins have no limits."""
    if user.is_admin:
        return Limits(unlimited=True)
    now = now or utcnow()
    sub = pk.current(db, user.id, now)
    pkg = db.get(Package, sub.package_id) if sub else None
    if pkg is None:
        sub, pkg = None, pk.free_package(db)
    base = ({"gpu_min_day": pkg.gpu_min_day, "gpu_min_month": pkg.gpu_min_month, "max_queued": pkg.max_queued}
            if pkg else dict(pk.FREE_FALLBACK))
    for key, col in (("gpu_min_day", "limit_gpu_min_day"), ("gpu_min_month", "limit_gpu_min_month"),
                     ("max_queued", "limit_max_queued")):
        if getattr(user, col) is not None:
            base[key] = getattr(user, col)
    grants = sum(g.gpu_min for g in pk.live_grants(db, user.id, now))
    return Limits(unlimited=False, package=pkg, subscription=sub, grant_min=grants,
                  models=list(pkg.models) if pkg and pkg.models is not None else None,
                  features=dict(pkg.features or {}) if pkg else {}, **base)


def used_minutes(db: Session, user_id: str, since: datetime) -> float:
    sec = db.scalar(select(func.coalesce(func.sum(UsageLedger.gpu_seconds), 0.0))
                    .where(UsageLedger.user_id == user_id, UsageLedger.created_at >= since))
    return float(sec or 0) / 60


def queued_count(db: Session, user_id: str) -> int:
    # top-level only: the children of one accepted job don't use up the user's queue
    rows = db.scalars(select(Job.payload).where(Job.user_id == user_id, Job.status.in_(("queued", "running"))))
    return sum(1 for p in rows if not _child_payload(p))


def _child_payload(payload: dict | None) -> bool:
    p = payload or {}
    return bool(p.get("parent_job_id") or p.get("inline_of"))


def usage(db: Session, user: User, now: datetime | None = None) -> dict:
    """GET /account/usage, and the admin's view of one user."""
    from app import rights

    now = now or utcnow()
    lim = limits_for(db, user, now)
    r = rights.effective(db, user, lim)
    sub = lim.subscription
    pkg = lim.package
    return {
        "package": {"id": pkg.id, "name": pkg.name, "kind": pkg.kind} if pkg else
                   ({"id": None, "name": "Unlimited", "kind": "admin"} if lim.unlimited else
                    {"id": None, "name": "Free", "kind": "standard"}),
        "unlimited": lim.unlimited,
        "gpu_min_day": lim.day_limit,
        "gpu_min_month": lim.month_limit,
        "max_queued": lim.max_queued,
        "grant_min": round(lim.grant_min, 1),
        "used_min_today": round(used_minutes(db, user.id, day_start(now)), 1),
        "used_min_month": round(used_minutes(db, user.id, month_start(now)), 1),
        "queued": queued_count(db, user.id),
        "resets_at": _iso(pk.end_of_day(now)),
        "month_resets_at": _iso(next_month(now)),
        "subscription": None if sub is None else {"id": sub.id, "status": pk.status_of(sub, now),
                                                  "starts_at": _iso(sub.starts_at), "ends_at": _iso(sub.ends_at),
                                                  "source": sub.source},
        "features": dict(r.features) if not r.admin else {k: True for k in pk.FEATURES},
        "models": r.models(),
    }


def _fmt(n: float) -> str:
    return f"{n:g}" if n == int(n) else f"{n:.1f}"


def check_minutes(db: Session, user: User, lim: Limits, now: datetime) -> None:
    if lim.unlimited:
        return
    used_day = used_minutes(db, user.id, day_start(now))
    if lim.day_limit is not None and used_day >= lim.day_limit:
        raise QuotaExceeded(
            f"You've used today's {_fmt(lim.day_limit)} GPU minutes. They come back at midnight UTC.",
            reason="day", used_min=used_day, limit_min=lim.day_limit, resets_at=pk.end_of_day(now))
    if lim.month_limit is not None:
        used_month = used_minutes(db, user.id, month_start(now))
        if used_month >= lim.month_limit:
            raise QuotaExceeded(
                f"You've used this month's {_fmt(lim.month_limit)} GPU minutes. They come back on the 1st.",
                reason="month", used_min=used_month, limit_min=lim.month_limit, resets_at=next_month(now))
    if lim.max_queued is not None:
        n = queued_count(db, user.id)
        if n >= lim.max_queued:
            word = "job" if lim.max_queued == 1 else "jobs"
            raise QuotaExceeded(
                f"You have {n} {word} on the way, the most your package allows at once. Start more when one "
                "finishes.", reason="queued", used_min=used_day, limit_min=lim.day_limit,
                resets_at=None, queued=n, max_queued=lim.max_queued)


# ---------------------------------------------------------------- features and models

_PREFIX_FEATURE = (("ad_", "ads"), ("poetry_", "poetry"), ("podcast_", "podcast"), ("video_podcast", "podcast"),
                   ("dub_", "dub"), ("story_", "stories"), ("character_", "character"),
                   ("ai_character", "character"), ("autopilot", "video"), ("reel_", "video"),
                   ("upscale", "video"), ("presenter", "presenter"),
                   ("audio_remix", "audio"), ("edit_set", "image"))


def _gen(db: Session, gen_id: str | None) -> Generation | None:
    if not gen_id:
        return None
    # still pending when it's added in the same flush as its job
    pending = next((o for o in db.new if isinstance(o, Generation) and o.id == gen_id), None)
    return pending or db.get(Generation, gen_id)


def features_of(db: Session, job: Job) -> set[str]:
    """Which studios a new top-level job belongs to. LLM-only and export jobs that fit none stay allowed."""
    out = {f for prefix, f in _PREFIX_FEATURE if job.type.startswith(prefix)}
    gen = _gen(db, job.generation_id)
    if gen is not None:
        if gen.target_type == "character":
            out.add("character")
        if gen.target_type == "ad_photo":
            out.add("ads")
        if (gen.target_type or "").startswith("implant_"):
            out.add("implant")
        if (gen.params or {}).get("sing_as"):
            out.add("song_voices")
        if gen.kind in IMAGE_KINDS:
            out.add("image")
        elif gen.kind in VIDEO_KINDS:
            out.add("video")
        elif gen.kind in AUDIO_KINDS:
            out.add("audio")
        elif gen.kind in MODEL_KINDS:
            out.add("3d")
    elif job.lane in ("image", "video", "audio"):
        out.add(job.lane)
    return out


def check_job(db: Session, lim: Limits, job: Job, channel: str | None, r=None) -> None:
    """The studios and the model a new job needs, against what this person may use (app.rights)."""
    if lim.unlimited:
        return
    if r is None:
        from app import rights

        r = rights.effective(db, db.get(User, job.user_id), lim) if job.user_id else None
    if r is None:
        return
    if channel == "api" and not r.allows("api"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, r.refusal("api") if "api" in r.feature_why
                            else "API and MCP aren't part of your plan")
    for f in sorted(features_of(db, job)):
        if not r.allows(f):
            raise HTTPException(status.HTTP_403_FORBIDDEN, r.refusal(f))
    if job.generation_id:
        gen = _gen(db, job.generation_id)
        model = (gen.params or {}).get("model_resolved") if gen else None
        if model:
            from app import rights

            rights.check_model(r, model)


@event.listens_for(Session, "before_flush")
def _check_new_jobs(session, _ctx, _instances):
    uid = session.info.get(QUOTA_USER)
    # an unconfirmed user hears about that first (security._gate_unverified)
    if not uid or session.info.get("unverified"):
        return
    jobs = [o for o in session.new if isinstance(o, Job) and not _child_payload(o.payload)]
    if not jobs:
        return
    with session.no_autoflush:
        user = session.get(User, uid)
        if user is None or user.is_admin:
            return
        from app import rights

        now = utcnow()
        lim = limits_for(session, user, now)
        r = rights.effective(session, user, lim)
        for job in jobs:
            check_job(session, lim, job, session.info.get(QUOTA_CHANNEL), r)
        if not session.info.get(_PASSED):
            check_minutes(session, user, lim, now)
            session.info[_PASSED] = True


@event.listens_for(Session, "after_commit")
def _forget_pass(session):
    session.info.pop(_PASSED, None)


@event.listens_for(Session, "after_soft_rollback")
def _forget_pass_on_rollback(session, _previous_transaction):
    session.info.pop(_PASSED, None)
