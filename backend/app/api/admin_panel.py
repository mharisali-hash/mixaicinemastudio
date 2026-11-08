"""The admin panel (contract v39): overview, packages, subscriptions, grants, payments, usage and the audit log.
Session only, admins only (404 for everyone else), and every change leaves an admin_audit row."""
import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from app import audit, models_catalog
from app import packages as pk
from app.api.admin import _naive_utc, _users_out
from app.db import get_db
from app.lanes import LANES
from app.models import (AdminAudit, Job, Package, Payment, Subscription, UsageGrant, UsageLedger, User,
                        WorkerHeartbeat, utcnow)
from app.package_schemas import (AdminUserDetail, AssignIn, AuditOut, CustomPackageIn, DemoIn, ExtendIn, GrantIn,
                                 GrantOut, PackageIn, PackageOut, PackagePatch, PaymentDetail, PaymentIn,
                                 PaymentMarkIn, PaymentOut, SubscriptionOut)
from app.quota import day_start, month_start, usage
from app.security import CurrentUser, require_admin, session_only

router = APIRouter(prefix="/admin", tags=["admin"])

LIVE_WORKER = timedelta(seconds=90)


def _fail(e: pk.PackageError):
    raise HTTPException(e.status, str(e)) from None


def _user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


def _package(db: Session, package_id: str | None) -> Package:
    p = db.get(Package, package_id) if package_id else None
    if p is None:
        raise HTTPException(404, "Package not found")
    return p


# ---------------------------------------------------------------- shaping

def _subs_out(db: Session, rows: list[Subscription]) -> list[SubscriptionOut]:
    now = utcnow()
    names = dict(db.execute(select(Package.id, Package.name)
                            .where(Package.id.in_({s.package_id for s in rows}))).all()) if rows else {}
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_({s.user_id for s in rows}))).all()) \
        if rows else {}
    return [SubscriptionOut(id=s.id, user_id=s.user_id, package_id=s.package_id,
                            package_name=names.get(s.package_id, ""), user_email=emails.get(s.user_id, ""),
                            starts_at=s.starts_at, ends_at=s.ends_at, status=pk.status_of(s, now), source=s.source,
                            notes=s.notes, granted_by=s.granted_by, created_at=s.created_at) for s in rows]


def _pays_out(db: Session, rows: list[Payment], detail: bool = False) -> list[PaymentOut]:
    names = dict(db.execute(select(Package.id, Package.name)
                            .where(Package.id.in_({p.package_id for p in rows if p.package_id}))).all())
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_({p.user_id for p in rows}))).all()) \
        if rows else {}
    cls = PaymentDetail if detail else PaymentOut
    out = []
    for p in rows:
        extra = {"raw": p.raw} if detail else {}
        out.append(cls(id=p.id, user_id=p.user_id, user_email=emails.get(p.user_id, ""),
                       subscription_id=p.subscription_id, package_id=p.package_id,
                       package_name=names.get(p.package_id, "") if p.package_id else "", gateway=p.gateway,
                       amount_pkr=p.amount_pkr, status=p.status, reference=p.reference, notes=p.notes,
                       created_at=p.created_at, updated_at=p.updated_at, **extra))
    return out


def _grant_out(g: UsageGrant, now: datetime) -> GrantOut:
    live = g.revoked_at is None and g.starts_at <= now and (g.expires_at is None or g.expires_at > now)
    return GrantOut.model_validate(g).model_copy(update={"live": live})


def _packages_out(db: Session, rows: list[Package]) -> list[PackageOut]:
    now = utcnow()
    counts: dict[str, int] = {}
    if rows:
        for s in db.scalars(select(Subscription).where(Subscription.status == "active",
                                                       Subscription.package_id.in_([p.id for p in rows]))):
            if pk.is_live(s, now):
                counts[s.package_id] = counts.get(s.package_id, 0) + 1
    return [PackageOut.model_validate(p).model_copy(update={"subscribers": counts.get(p.id, 0),
                                                            "features": dict(p.features or {})})
            for p in rows]


def _check_package_fields(data: dict) -> None:
    models = data.get("models")
    if models is not None:
        unknown = [m for m in models if m not in models_catalog.MODELS]
        if unknown:
            raise HTTPException(422, f"Unknown model: {', '.join(unknown[:5])}")
    feats = data.get("features")
    if feats:
        unknown = [f for f in feats if f not in pk.FEATURES]
        if unknown:
            raise HTTPException(422, f"Unknown feature: {', '.join(unknown[:5])}")


# ---------------------------------------------------------------- overview

@router.get("/overview")
@session_only
def overview(db: Session = Depends(get_db), _=Depends(require_admin)):
    now = utcnow()
    today = day_start(now)
    month = month_start(now)
    total = db.scalar(select(func.count()).select_from(User)) or 0
    new_week = db.scalar(select(func.count()).select_from(User).where(User.created_at >= now - timedelta(days=7))) or 0
    signed_in = set(db.scalars(select(User.id).where(User.last_login_at >= today)))
    worked = set(db.scalars(select(Job.user_id).distinct()
                            .where(Job.created_at >= today, Job.user_id.is_not(None))))

    by_package: dict[str, int] = {}
    for s in db.scalars(select(Subscription).where(Subscription.status == "active")):
        if pk.is_live(s, now):
            by_package[s.package_id] = by_package.get(s.package_id, 0) + 1
    names = dict(db.execute(select(Package.id, Package.name).where(Package.id.in_(by_package))).all())

    paid = db.execute(select(func.count(), func.coalesce(func.sum(Payment.amount_pkr), 0))
                      .where(Payment.status == "paid", Payment.updated_at >= month)).one()
    pending = db.scalar(select(func.count()).select_from(Payment).where(Payment.status == "pending")) or 0

    top = db.execute(select(UsageLedger.user_id, func.sum(UsageLedger.gpu_seconds).label("sec"))
                     .where(UsageLedger.created_at >= today, UsageLedger.user_id.is_not(None))
                     .group_by(UsageLedger.user_id).order_by(func.sum(UsageLedger.gpu_seconds).desc())
                     .limit(10)).all()
    people = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r[0] for r in top])))}

    lanes = {lane: {"queued": 0, "running": 0} for lane in LANES}
    for lane, st, n in db.execute(select(Job.lane, Job.status, func.count())
                                  .where(Job.status.in_(("queued", "running"))).group_by(Job.lane, Job.status)):
        lanes.setdefault(lane, {"queued": 0, "running": 0})[st] = n
    beats = db.scalars(select(WorkerHeartbeat)).all()
    live = [hb for hb in beats if hb.last_seen >= now - LIVE_WORKER]
    served = sorted({lane for hb in live for lane in ((hb.info or {}).get("lanes") or LANES)})
    return {
        "users": {"total": total, "new_week": new_week, "active_today": len(signed_in | worked)},
        "subscriptions": sorted(({"package_id": k, "name": names.get(k, "?"), "count": v}
                                 for k, v in by_package.items()), key=lambda r: -r["count"]),
        "payments_month": {"count": int(paid[0]), "paid_pkr": int(paid[1] or 0), "pending": pending},
        "gpu_today": [{"user_id": uid, "email": people[uid].email if uid in people else "",
                       "display_name": people[uid].display_name if uid in people else "",
                       "gpu_min": round(float(sec or 0) / 60, 1)} for uid, sec in top],
        "queue": {"queued": sum(v["queued"] for v in lanes.values()),
                  "running": sum(v["running"] for v in lanes.values()), "lanes": lanes},
        "workers": {"live": len(live), "stale": len(beats) - len(live), "lanes": served,
                    "idle_lanes": [lane for lane in LANES if lane not in served]},
    }


# ---------------------------------------------------------------- one user

@router.get("/users/{user_id}", response_model=AdminUserDetail)
@session_only
def user_detail(user_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = _user(db, user_id)
    now = utcnow()
    subs = list(db.scalars(select(Subscription).where(Subscription.user_id == user.id)
                           .order_by(Subscription.created_at.desc()).limit(50)))
    current = pk.current(db, user.id, now)
    grants = db.scalars(select(UsageGrant).where(UsageGrant.user_id == user.id)
                        .order_by(UsageGrant.created_at.desc()).limit(50)).all()
    pays = list(db.scalars(select(Payment).where(Payment.user_id == user.id)
                           .order_by(Payment.created_at.desc()).limit(50)))
    jobs = db.scalars(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(20)).all()
    return AdminUserDetail(
        user=_users_out(db, [user])[0],
        usage=usage(db, user, now),
        limits_override={"gpu_min_day": user.limit_gpu_min_day, "gpu_min_month": user.limit_gpu_min_month,
                         "max_queued": user.limit_max_queued},
        subscription=_subs_out(db, [current])[0] if current else None,
        subscriptions=_subs_out(db, subs),
        grants=[_grant_out(g, now) for g in grants],
        payments=_pays_out(db, pays),
        recent_jobs=[{"id": j.id, "type": j.type, "status": j.status, "lane": j.lane, "via_api": bool(j.via_api),
                      "created_at": j.created_at.isoformat() + "Z", "gpu_min": round((j.gpu_seconds or 0) / 60, 2),
                      "error": j.error} for j in jobs],
        had_demo=pk.had_demo(db, user.id),
    )


@router.post("/users/{user_id}/subscription", response_model=SubscriptionOut, status_code=201)
@session_only
def assign_package(user_id: str, body: AssignIn, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_admin)):
    user = _user(db, user_id)
    pkg = _package(db, body.package_id)
    try:
        sub = pk.assign(db, user, pkg, starts_at=_naive_utc(body.starts_at), ends_at=_naive_utc(body.ends_at),
                        notes=body.notes, granted_by=cur.user.id,
                        source="demo" if pkg.kind == "demo" else "admin")
    except pk.PackageError as e:
        _fail(e)
    audit.record(db, cur.user, "subscription_assign", "user", user.id, package=pkg.name, subscription_id=sub.id,
                 starts_at=sub.starts_at, ends_at=sub.ends_at)
    db.commit()
    return _subs_out(db, [sub])[0]


@router.post("/users/{user_id}/demo", response_model=SubscriptionOut, status_code=201)
@session_only
def start_demo(user_id: str, body: DemoIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    user = _user(db, user_id)
    pkg = _package(db, body.package_id) if body.package_id else None
    try:
        sub = pk.start_demo(db, user, pkg, allow_repeat=body.allow_repeat, granted_by=cur.user.id, notes=body.notes)
    except pk.PackageError as e:
        _fail(e)
    audit.record(db, cur.user, "demo_start", "user", user.id, subscription_id=sub.id, ends_at=sub.ends_at,
                 repeat=body.allow_repeat or None)
    db.commit()
    return _subs_out(db, [sub])[0]


@router.post("/users/{user_id}/grants", response_model=GrantOut, status_code=201)
@session_only
def grant_minutes(user_id: str, body: GrantIn, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_admin)):
    user = _user(db, user_id)
    try:
        g = pk.grant(db, user, body.gpu_min, reason=body.reason, expires_at=_naive_utc(body.expires_at),
                     granted_by=cur.user.id)
    except pk.PackageError as e:
        _fail(e)
    audit.record(db, cur.user, "grant_add", "user", user.id, grant_id=g.id, gpu_min=g.gpu_min, reason=g.reason,
                 expires_at=g.expires_at)
    db.commit()
    return _grant_out(g, utcnow())


@router.post("/grants/{grant_id}/revoke", response_model=GrantOut)
@session_only
def revoke_grant(grant_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    g = db.get(UsageGrant, grant_id)
    if g is None:
        raise HTTPException(404, "Grant not found")
    if g.revoked_at is None:
        g.revoked_at = utcnow()
        audit.record(db, cur.user, "grant_revoke", "user", g.user_id, grant_id=g.id, gpu_min=g.gpu_min)
        db.commit()
    return _grant_out(g, utcnow())


@router.post("/users/{user_id}/custom-package", response_model=SubscriptionOut, status_code=201)
@session_only
def custom_package(user_id: str, body: CustomPackageIn, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_admin)):
    """A package made for one person, and put on them straight away."""
    user = _user(db, user_id)
    data = body.model_dump(exclude={"starts_at", "ends_at", "custom_for_user_id", "kind", "public"})
    _check_package_fields(data)
    pkg = Package(**data, kind="custom", public=False, custom_for_user_id=user.id)
    db.add(pkg)
    db.flush()
    try:
        sub = pk.assign(db, user, pkg, starts_at=_naive_utc(body.starts_at), ends_at=_naive_utc(body.ends_at),
                        granted_by=cur.user.id)
    except pk.PackageError as e:
        _fail(e)
    audit.record(db, cur.user, "package_custom", "user", user.id, package_id=pkg.id, package=pkg.name,
                 subscription_id=sub.id)
    db.commit()
    return _subs_out(db, [sub])[0]


# ---------------------------------------------------------------- subscriptions

@router.get("/subscriptions", response_model=list[SubscriptionOut])
@session_only
def list_subscriptions(status: str = Query("active", pattern="^(all|active|expiring|expired|cancelled|pending_payment)$"),
                       package_id: str = "", user_id: str = "", limit: int = Query(200, ge=1, le=1000),
                       db: Session = Depends(get_db), _=Depends(require_admin)):
    now = utcnow()
    # lapsed rows say so before they're listed
    if pk.expire_due(db, now):
        db.commit()
    q = select(Subscription)
    if status == "expiring":
        q = q.where(Subscription.status == "active", Subscription.ends_at.is_not(None),
                    Subscription.ends_at > now, Subscription.ends_at <= now + timedelta(days=7))
        order = Subscription.ends_at
    else:
        if status != "all":
            q = q.where(Subscription.status == status)
        order = Subscription.created_at.desc()
    if package_id:
        q = q.where(Subscription.package_id == package_id)
    if user_id:
        q = q.where(Subscription.user_id == user_id)
    return _subs_out(db, list(db.scalars(q.order_by(order).limit(limit))))


def _sub(db: Session, sub_id: str) -> Subscription:
    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(404, "Subscription not found")
    return sub


@router.post("/subscriptions/{sub_id}/extend", response_model=SubscriptionOut)
@session_only
def extend_subscription(sub_id: str, body: ExtendIn, db: Session = Depends(get_db),
                        cur: CurrentUser = Depends(require_admin)):
    sub = _sub(db, sub_id)
    before = sub.ends_at
    try:
        pk.extend(db, sub, days=body.days, ends_at=_naive_utc(body.ends_at))
    except pk.PackageError as e:
        _fail(e)
    audit.record(db, cur.user, "subscription_extend", "user", sub.user_id, subscription_id=sub.id, was=before,
                 ends_at=sub.ends_at)
    db.commit()
    return _subs_out(db, [sub])[0]


@router.post("/subscriptions/{sub_id}/cancel", response_model=SubscriptionOut)
@session_only
def cancel_subscription(sub_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    sub = _sub(db, sub_id)
    try:
        pk.cancel(sub)
    except pk.PackageError as e:
        _fail(e)
    audit.record(db, cur.user, "subscription_cancel", "user", sub.user_id, subscription_id=sub.id)
    db.commit()
    return _subs_out(db, [sub])[0]


# ---------------------------------------------------------------- packages

@router.get("/packages", response_model=list[PackageOut])
@session_only
def list_packages(archived: bool = False, custom: bool = True, db: Session = Depends(get_db),
                  _=Depends(require_admin)):
    q = select(Package)
    if not archived:
        q = q.where(Package.active.is_(True))
    if not custom:
        q = q.where(Package.custom_for_user_id.is_(None))
    return _packages_out(db, list(db.scalars(q.order_by(Package.sort, Package.created_at))))


@router.post("/packages", response_model=PackageOut, status_code=201)
@session_only
def create_package(body: PackageIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    data = body.model_dump()
    _check_package_fields(data)
    if data.get("custom_for_user_id"):
        _user(db, data["custom_for_user_id"])
        data.update(kind="custom", public=False)
    pkg = Package(**data)
    db.add(pkg)
    db.flush()
    audit.record(db, cur.user, "package_create", "package", pkg.id, name=pkg.name, kind=pkg.kind)
    db.commit()
    return _packages_out(db, [pkg])[0]


@router.patch("/packages/{package_id}", response_model=PackageOut)
@session_only
def patch_package(package_id: str, body: PackagePatch, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_admin)):
    pkg = _package(db, package_id)
    sent = body.model_dump(exclude_unset=True)
    _check_package_fields(sent)
    for key in ("name", "price_pkr", "period", "kind", "public", "active", "sort", "private_items", "description"):
        if key in sent and sent[key] is None:
            sent.pop(key)
    if pkg.slug == pk.FREE and sent.get("active") is False:
        raise HTTPException(422, "Free is everyone's fallback, so it can't be archived")
    for key, value in sent.items():
        setattr(pkg, key, {} if key == "features" and value is None else value)
    # sent may carry the new name itself
    audit.record(db, cur.user, "package_update", "package", pkg.id, **{"name": pkg.name, **sent})
    db.commit()
    return _packages_out(db, [pkg])[0]


@router.post("/packages/{package_id}/duplicate", response_model=PackageOut, status_code=201)
@session_only
def duplicate_package(package_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    src = _package(db, package_id)
    copy = Package(name=f"{src.name} (copy)"[:120], description=src.description, price_pkr=src.price_pkr,
                   period=src.period, days=src.days, gpu_min_day=src.gpu_min_day, gpu_min_month=src.gpu_min_month,
                   max_queued=src.max_queued, private_items=src.private_items,
                   models=list(src.models) if src.models is not None else None, features=dict(src.features or {}),
                   kind="standard" if src.kind == "custom" else src.kind, public=False, active=True,
                   sort=src.sort + 1)
    db.add(copy)
    db.flush()
    audit.record(db, cur.user, "package_duplicate", "package", copy.id, source=src.id, name=copy.name)
    db.commit()
    return _packages_out(db, [copy])[0]


@router.post("/packages/{package_id}/archive", response_model=PackageOut)
@session_only
def archive_package(package_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    pkg = _package(db, package_id)
    if pkg.slug == pk.FREE:
        raise HTTPException(422, "Free is everyone's fallback, so it can't be archived")
    # people already on it keep it until it ends; it just can't be given out any more
    pkg.active = False
    audit.record(db, cur.user, "package_archive", "package", pkg.id, name=pkg.name)
    db.commit()
    return _packages_out(db, [pkg])[0]


@router.post("/packages/{package_id}/restore", response_model=PackageOut)
@session_only
def restore_package(package_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    pkg = _package(db, package_id)
    pkg.active = True
    audit.record(db, cur.user, "package_restore", "package", pkg.id, name=pkg.name)
    db.commit()
    return _packages_out(db, [pkg])[0]


# ---------------------------------------------------------------- payments

@router.get("/payments", response_model=list[PaymentOut])
@session_only
def list_payments(status: str = Query("all", pattern="^(all|pending|paid|failed|refunded)$"), gateway: str = "",
                  user_id: str = "", limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db),
                  _=Depends(require_admin)):
    q = select(Payment)
    if status != "all":
        q = q.where(Payment.status == status)
    if gateway:
        q = q.where(Payment.gateway == gateway)
    if user_id:
        q = q.where(Payment.user_id == user_id)
    return _pays_out(db, list(db.scalars(q.order_by(Payment.created_at.desc()).limit(limit))))


@router.get("/payments/{payment_id}", response_model=PaymentDetail)
@session_only
def get_payment(payment_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    p = db.get(Payment, payment_id)
    if p is None:
        raise HTTPException(404, "Payment not found")
    return _pays_out(db, [p], detail=True)[0]


@router.post("/payments", response_model=PaymentDetail, status_code=201)
@session_only
def record_payment(body: PaymentIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    """A payment taken outside the studio. With a package it waits on that package (pending), or puts the user
    on it at once (paid)."""
    user = _user(db, body.user_id)
    sub = db.get(Subscription, body.subscription_id) if body.subscription_id else None
    if body.subscription_id and (sub is None or sub.user_id != user.id):
        raise HTTPException(404, "Subscription not found")
    if body.status in ("failed", "refunded"):
        raise HTTPException(422, "Record it as pending or paid")
    pkg = _package(db, body.package_id) if body.package_id else None
    if pkg is not None and sub is None:
        try:
            sub = pk.assign(db, user, pkg, source="payment", granted_by=cur.user.id,
                            status="active" if body.status == "paid" else "pending_payment")
        except pk.PackageError as e:
            _fail(e)
    p = Payment(user_id=user.id, subscription_id=sub.id if sub else None,
                package_id=pkg.id if pkg else (sub.package_id if sub else None), gateway=body.gateway.strip(),
                amount_pkr=body.amount_pkr, status=body.status, reference=body.reference.strip(),
                notes=body.notes.strip(), raw=None)
    db.add(p)
    db.flush()
    audit.record(db, cur.user, "payment_record", "payment", p.id, user_id=user.id, amount_pkr=p.amount_pkr,
                 status=p.status, gateway=p.gateway)
    db.commit()
    return _pays_out(db, [p], detail=True)[0]


@router.post("/payments/{payment_id}/mark", response_model=PaymentDetail)
@session_only
def mark_payment(payment_id: str, body: PaymentMarkIn, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_admin)):
    p = db.get(Payment, payment_id)
    if p is None:
        raise HTTPException(404, "Payment not found")
    allowed = {"paid": ("pending", "failed"), "failed": ("pending",), "refunded": ("paid",)}[body.status]
    if p.status not in allowed:
        raise HTTPException(409, f"A {p.status} payment can't be marked {body.status}")
    was = p.status
    p.status = body.status
    if body.notes.strip():
        p.notes = (f"{p.notes}\n{body.notes.strip()}" if p.notes else body.notes.strip())[:1000]
    sub = db.get(Subscription, p.subscription_id) if p.subscription_id else None
    if body.status == "paid" and sub is not None and sub.status == "pending_payment":
        pk.activate(db, sub)
    audit.record(db, cur.user, f"payment_{body.status}", "payment", p.id, user_id=p.user_id, was=was,
                 amount_pkr=p.amount_pkr, subscription_id=sub.id if sub else None)
    db.commit()
    return _pays_out(db, [p], detail=True)[0]


# ---------------------------------------------------------------- usage

def _usage_rows(db: Session, days: int, user_id: str) -> list[dict]:
    since = day_start(utcnow()) - timedelta(days=days - 1)
    day = func.date(UsageLedger.created_at)
    q = (select(day.label("day"), UsageLedger.user_id, func.sum(UsageLedger.gpu_seconds),
                func.count(distinct(UsageLedger.job_id)))
         .where(UsageLedger.created_at >= since).group_by(day, UsageLedger.user_id)
         .order_by(day.desc(), func.sum(UsageLedger.gpu_seconds).desc()))
    if user_id:
        q = q.where(UsageLedger.user_id == user_id)
    rows = db.execute(q).all()
    people = {u.id: u for u in db.scalars(select(User).where(User.id.in_({r[1] for r in rows if r[1]})))}
    return [{"day": str(d), "user_id": uid, "email": people[uid].email if uid in people else "",
             "display_name": people[uid].display_name if uid in people else "",
             "gpu_min": round(float(sec or 0) / 60, 2), "jobs": int(n or 0)} for d, uid, sec, n in rows]


@router.get("/usage")
@session_only
def usage_report(days: int = Query(30, ge=1, le=366), user_id: str = "", db: Session = Depends(get_db),
                 _=Depends(require_admin)):
    rows = _usage_rows(db, days, user_id)
    totals: dict[str, dict] = {}
    for r in rows:
        t = totals.setdefault(r["user_id"] or "", {"user_id": r["user_id"], "email": r["email"],
                                                   "display_name": r["display_name"], "gpu_min": 0.0, "jobs": 0})
        t["gpu_min"] = round(t["gpu_min"] + r["gpu_min"], 2)
        t["jobs"] += r["jobs"]
    top = sorted(totals.values(), key=lambda t: -t["gpu_min"])[:20]
    return {"days": days, "rows": rows, "top": top, "total_min": round(sum(r["gpu_min"] for r in rows), 2)}


@router.get("/usage.csv")
@session_only
def usage_csv(days: int = Query(30, ge=1, le=366), user_id: str = "", db: Session = Depends(get_db),
              _=Depends(require_admin)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["day", "user_id", "email", "name", "gpu_minutes", "jobs"])
    for r in _usage_rows(db, days, user_id):
        w.writerow([r["day"], r["user_id"] or "", r["email"], r["display_name"], r["gpu_min"], r["jobs"]])
    stamp = utcnow().strftime("%Y-%m-%d")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="gpu-usage-{stamp}.csv"'})


# ---------------------------------------------------------------- audit log

@router.get("/audit", response_model=list[AuditOut])
@session_only
def audit_log(action: str = "", target_id: str = "", q: str = "", limit: int = Query(200, ge=1, le=1000),
              db: Session = Depends(get_db), _=Depends(require_admin)):
    query = select(AdminAudit)
    if action:
        query = query.where(AdminAudit.action.like(f"{action}%"))
    if target_id:
        query = query.where(AdminAudit.target_id == target_id)
    if q.strip():
        query = query.where(func.lower(AdminAudit.actor_email).like(f"%{q.strip().lower()}%"))
    return list(db.scalars(query.order_by(AdminAudit.created_at.desc()).limit(limit)))
