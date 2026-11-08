"""Admin-only housekeeping (contract v34) and the users list (v38). Signed-in studio session only; hidden from
the API docs."""
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import accounts, mailer
from app import audit
from app import packages as pk
from app import visibility as vis
from app.db import get_db
from app.models import ApiToken, Job, Membership, Package, Subscription, UsageLedger, User, utcnow
from app.schemas import AdminUserCreate, AdminUserOut, AdminUserPatch
from app.security import CurrentUser, get_current_user, is_admin, require_admin, session_only

router = APIRouter(prefix="/admin", tags=["admin"])


class MarkApiIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=2000)
    since: datetime | None = None
    until: datetime | None = None
    kinds: list[str] | None = None
    apply: bool = False


def _naive_utc(at: datetime | None) -> datetime | None:
    if at is None or at.tzinfo is None:
        return at
    return at.astimezone(timezone.utc).replace(tzinfo=None)


@router.post("/mark-api")
@session_only
def mark_api(body: MarkApiIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    """Mark rows made through the API before via_api existed. A dry run unless apply is true."""
    if not is_admin(cur):
        raise HTTPException(404, "Not Found")
    try:
        out = vis.mark(db, workspace_id=cur.workspace_id, ids=[i.strip() for i in body.ids if i.strip()],
                       since=_naive_utc(body.since), until=_naive_utc(body.until), kinds=body.kinds,
                       apply=body.apply)
    except vis.MarkError as e:
        raise HTTPException(422, str(e)) from None
    if body.apply:
        audit.record(db, cur.user, "mark_api", "workspace", cur.workspace_id, ids=len(body.ids), kinds=body.kinds)
        db.commit()
    return out


# ---------------------------------------------------------------- users (contract v38, M17 step 1)

def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _users_out(db: Session, users: list[User]) -> list[AdminUserOut]:
    ids = [u.id for u in users]
    if not ids:
        return []
    now = utcnow()
    today, month = _day_start(now), _day_start(now).replace(day=1)

    def gpu_since(since: datetime) -> dict[str, float]:
        rows = db.execute(select(UsageLedger.user_id, func.coalesce(func.sum(UsageLedger.gpu_seconds), 0))
                          .where(UsageLedger.user_id.in_(ids), UsageLedger.created_at >= since)
                          .group_by(UsageLedger.user_id)).all()
        return {uid: float(sec) / 60 for uid, sec in rows}

    day, mon = gpu_since(today), gpu_since(month)
    subs = pk.current_many(db, ids, now)
    pkgs = {p.id: p for p in db.scalars(select(Package).where(Package.id.in_({s.package_id for s in subs.values()})))}
    free = pk.free_package(db)

    def plan(u: User) -> tuple[Package | None, Subscription | None]:
        sub = subs.get(u.id)
        return (pkgs.get(sub.package_id) if sub else free), sub

    def day_limit(u: User, p: Package | None) -> float | None:
        if u.is_admin:
            return None
        if u.limit_gpu_min_day is not None:
            return u.limit_gpu_min_day
        return p.gpu_min_day if p else pk.FREE_FALLBACK["gpu_min_day"]
    jobs = dict(db.execute(select(Job.user_id, func.count()).where(Job.user_id.in_(ids)).group_by(Job.user_id)).all())
    tokens = dict(db.execute(select(ApiToken.user_id, func.count())
                             .where(ApiToken.user_id.in_(ids), ApiToken.revoked_at.is_(None))
                             .group_by(ApiToken.user_id)).all())
    homes: dict[str, str] = {}
    for uid, ws in db.execute(select(Membership.user_id, Membership.workspace_id)
                              .where(Membership.user_id.in_(ids)).order_by(Membership.created_at)).all():
        homes.setdefault(uid, ws)
    out = []
    for u in users:
        p, sub = plan(u)
        out.append(AdminUserOut(
            id=u.id, email=u.email, display_name=u.display_name, is_admin=bool(u.is_admin),
            is_active=bool(u.is_active), verified=u.email_verified_at is not None, workspace_id=homes.get(u.id),
            created_at=u.created_at, last_login_at=u.last_login_at, gpu_min_today=round(day.get(u.id, 0.0), 1),
            gpu_min_month=round(mon.get(u.id, 0.0), 1), jobs=int(jobs.get(u.id, 0)), tokens=int(tokens.get(u.id, 0)),
            package_id=p.id if p and not u.is_admin else None,
            package_name="Unlimited" if u.is_admin else (p.name if p else "Free"),
            subscription_ends_at=sub.ends_at if sub else None, gpu_min_day_limit=day_limit(u, p),
            implant_access=bool(u.implant_access), staff_role=u.staff_role, blocked_at=u.blocked_at,
            blocked_reason=u.blocked_reason or ""))
    return out


@router.get("/users", response_model=list[AdminUserOut])
@session_only
def list_users(q: str = "", status: str = Query("all", pattern="^(all|active|inactive|unverified|admin)$"),
               package_id: str = "", limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db),
               _=Depends(require_admin)):
    query = select(User)
    if package_id:
        now = utcnow()
        live = (select(Subscription.user_id).where(
            Subscription.status == "active", Subscription.starts_at <= now,
            or_(Subscription.ends_at.is_(None), Subscription.ends_at > now)))
        free = pk.free_package(db)
        on_it = User.id.in_(live.where(Subscription.package_id == package_id))
        # Free is also everyone with nothing active
        query = query.where(User.is_admin.is_(False),
                            or_(on_it, User.id.not_in(live)) if free and free.id == package_id else on_it)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(User.email).like(like), func.lower(User.display_name).like(like)))
    if status == "active":
        query = query.where(User.is_active.is_(True))
    elif status == "inactive":
        query = query.where(User.is_active.is_(False))
    elif status == "unverified":
        query = query.where(User.email_verified_at.is_(None))
    elif status == "admin":
        query = query.where(User.is_admin.is_(True))
    users = db.scalars(query.order_by(User.created_at.desc()).limit(limit)).all()
    return _users_out(db, list(users))


@router.post("/users", response_model=AdminUserOut, status_code=201)
@session_only
def create_user(body: AdminUserCreate, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    """An account made by the admin (the way in while SIGNUP=invite); the invite email sets the password."""
    if not accounts.looks_like_email(accounts.normalise_email(body.email)):
        raise HTTPException(422, "That doesn't look like an email address")
    try:
        user = accounts.create_account(db, body.email, body.name, admin=body.admin)
    except accounts.AccountError as e:
        raise HTTPException(409 if isinstance(e, accounts.AccountExists) else 422, str(e)) from None
    raw = accounts.issue_token(db, user, "invite")
    audit.record(db, cur.user, "user_create", "user", user.id, email=user.email, admin=body.admin)
    db.commit()
    mailer.deliver(mailer.invite_mail(user.email, user.display_name,
                                      mailer.link(f"/reset-password?token={quote(raw)}&invite=1")))
    return _users_out(db, [user])[0]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
@session_only
def patch_user(user_id: str, body: AdminUserPatch, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    own = user.id == cur.user.id
    # the admin can't lock themselves out by accident; another admin has to do it
    if own and (body.is_active is False or body.is_admin is False):
        raise HTTPException(422, "You can't switch off or demote your own account")
    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.display_name
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.verified is not None:
        user.email_verified_at = (user.email_verified_at or utcnow()) if body.verified else None
    if body.implant_access is not None:
        user.implant_access = body.implant_access
    if body.is_active is False and user.is_active:
        accounts.deactivate(db, user)
    elif body.is_active is True:
        user.is_active = True
    # explicit null clears an override, so look at what was sent rather than at the values
    sent = body.model_dump(exclude_unset=True)
    for col in ("limit_gpu_min_day", "limit_gpu_min_month", "limit_max_queued"):
        if col in sent:
            setattr(user, col, sent[col])
    audit.record(db, cur.user, "user_update", "user", user.id, **sent)
    db.commit()
    return _users_out(db, [user])[0]


@router.post("/users/{user_id}/send-reset", status_code=202)
@session_only
def send_reset(user_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(404, "User not found")
    raw = accounts.issue_token(db, user, "reset")
    audit.record(db, cur.user, "user_send_reset", "user", user.id)
    db.commit()
    mailer.deliver(mailer.reset_mail(user.email, mailer.link(f"/reset-password?token={quote(raw)}")))
    return {"detail": f"A reset link is on its way to {user.email}"}


@router.post("/users/{user_id}/resend-verification", status_code=202)
@session_only
def resend_verification(user_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(404, "User not found")
    if user.email_verified_at is not None:
        raise HTTPException(422, "That email is already confirmed")
    raw = accounts.issue_token(db, user, "verify")
    audit.record(db, cur.user, "user_resend_verification", "user", user.id)
    db.commit()
    mailer.deliver(mailer.verify_mail(user.email, user.display_name,
                                      mailer.link(f"/api/auth/verify?token={raw}")))
    return {"detail": f"A new confirmation link is on its way to {user.email}"}
