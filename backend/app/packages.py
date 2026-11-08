"""Packages, subscriptions and usage grants (contract v39). The admin panel and quota both go through here."""
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Package, Subscription, UsageGrant, User, utcnow

FREE = "free"
PERIOD_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
PERIODS = ("day", "week", "month", "year", "none")
KINDS = ("standard", "demo", "custom")
STATUSES = ("active", "expired", "cancelled", "pending_payment")
SOURCES = ("admin", "demo", "payment", "signup_default")
# what package.features can switch off; anything a package doesn't name stays on once it's released
# (app.rights: a studio nobody has released yet is admin-only, whatever the package says)
FEATURES = {
    "image": "Image studio",
    "video": "Video studio",
    "audio": "Audio studio",
    "character": "Character Studio",
    "ads": "Ad Studio",
    "poetry": "Poetry Shorts",
    "podcast": "Podcasts",
    "dub": "Dubbing",
    "stories": "Stories",
    "3d": "3D Studio",
    "api": "API and MCP",
    "song_voices": "Song voices",
    "presenter": "Presenter Video",
    "print": "Print Studio",
    "implant": "Implant Preview",
    "bulk_edit": "Bulk edit",
}
# the fallback when the Free row is missing (a fresh test database before seeding, or one deleted by hand)
FREE_FALLBACK = dict(gpu_min_day=30.0, gpu_min_month=None, max_queued=2)

DEFAULTS = (
    dict(slug="free", name="Free", description="For trying the studio out.", price_pkr=0, period="none",
         gpu_min_day=30.0, max_queued=2, kind="standard", public=True, sort=0),
    dict(slug="demo", name="Demo (7 days)", description="A week with room to explore.", price_pkr=0,
         period="none", days=7, gpu_min_day=120.0, max_queued=4, kind="demo", public=False, sort=1),
    dict(slug="pro", name="Pro", description="For regular work.", price_pkr=0, period="month",
         gpu_min_day=240.0, max_queued=6, kind="standard", public=True, sort=2),
)


class PackageError(ValueError):
    status = 422


class Conflict(PackageError):
    status = 409


def ensure_defaults(db: Session) -> None:
    """Free, Demo and Pro on a database that has none (migration 0028 seeds the same on upgrades)."""
    if db.scalars(select(Package.id).limit(1)).first():
        return
    db.add_all(Package(features={}, **d) for d in DEFAULTS)
    db.flush()


def free_package(db: Session) -> Package | None:
    return db.scalars(select(Package).where(Package.slug == FREE)).first()


def is_live(sub: Subscription, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return sub.status == "active" and sub.starts_at <= now and (sub.ends_at is None or sub.ends_at > now)


def status_of(sub: Subscription, now: datetime | None = None) -> str:
    # what it is right now, before the sweep has caught up with it
    now = now or utcnow()
    if sub.status == "active" and sub.ends_at is not None and sub.ends_at <= now:
        return "expired"
    return sub.status


def current(db: Session, user_id: str, now: datetime | None = None) -> Subscription | None:
    now = now or utcnow()
    rows = db.scalars(select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
                      .order_by(Subscription.starts_at.desc())).all()
    return next((s for s in rows if is_live(s, now)), None)


def current_many(db: Session, user_ids: list[str], now: datetime | None = None) -> dict[str, Subscription]:
    now = now or utcnow()
    out: dict[str, Subscription] = {}
    if not user_ids:
        return out
    for s in db.scalars(select(Subscription).where(Subscription.user_id.in_(user_ids),
                                                   Subscription.status == "active")
                        .order_by(Subscription.starts_at.desc())):
        if s.user_id not in out and is_live(s, now):
            out[s.user_id] = s
    return out


def expire_due(db: Session, now: datetime | None = None) -> int:
    """Mark active subscriptions whose end has passed as expired; their users drop back to Free."""
    now = now or utcnow()
    res = db.execute(update(Subscription)
                     .where(Subscription.status == "active", Subscription.ends_at.is_not(None),
                            Subscription.ends_at <= now)
                     .values(status="expired", updated_at=now)
                     .execution_options(synchronize_session=False))
    return res.rowcount or 0


def default_end(pkg: Package, starts_at: datetime) -> datetime | None:
    days = PERIOD_DAYS.get(pkg.period) if pkg.period != "none" else pkg.days
    if pkg.kind == "demo" and pkg.days:
        days = pkg.days
    return starts_at + timedelta(days=days) if days else None


def assign(db: Session, user: User, pkg: Package, *, starts_at: datetime | None = None,
           ends_at: datetime | None = None, source: str = "admin", notes: str = "",
           granted_by: str | None = None, status: str = "active") -> Subscription:
    """The user's one active subscription from now on; whatever was active is cancelled."""
    if not pkg.active:
        raise PackageError("That package is archived")
    if pkg.custom_for_user_id and pkg.custom_for_user_id != user.id:
        raise PackageError("That package was made for someone else")
    now = utcnow()
    starts_at = starts_at or now
    if ends_at is None:
        ends_at = default_end(pkg, starts_at)
    if ends_at is not None and ends_at <= starts_at:
        raise PackageError("The end has to come after the start")
    if status == "active":
        db.execute(update(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active")
                   .values(status="cancelled", updated_at=now).execution_options(synchronize_session="fetch"))
    sub = Subscription(user_id=user.id, package_id=pkg.id, starts_at=starts_at, ends_at=ends_at, status=status,
                       source=source, notes=notes.strip()[:1000], granted_by=granted_by)
    db.add(sub)
    db.flush()
    return sub


def had_demo(db: Session, user_id: str) -> bool:
    return db.scalars(select(Subscription.id).where(Subscription.user_id == user_id, Subscription.source == "demo")
                      .limit(1)).first() is not None


def start_demo(db: Session, user: User, pkg: Package | None = None, *, allow_repeat: bool = False,
               granted_by: str | None = None, notes: str = "") -> Subscription:
    if pkg is None:
        pkg = db.scalars(select(Package).where(Package.kind == "demo", Package.active.is_(True))
                         .order_by(Package.sort)).first()
        if pkg is None:
            raise PackageError("There's no demo package to start")
    if pkg.kind != "demo":
        raise PackageError("That isn't a demo package")
    # one demo each; the admin can say yes to another on purpose
    if not allow_repeat and had_demo(db, user.id):
        raise Conflict("They've had a demo already")
    return assign(db, user, pkg, source="demo", granted_by=granted_by, notes=notes)


def extend(db: Session, sub: Subscription, *, days: int | None = None, ends_at: datetime | None = None) -> None:
    now = utcnow()
    if ends_at is None:
        if not days:
            raise PackageError("Say how many days, or the new end")
        ends_at = max(sub.ends_at or now, now) + timedelta(days=days)
    if ends_at <= now:
        raise PackageError("The new end has to be in the future")
    if sub.status in ("expired", "cancelled"):
        other = current(db, sub.user_id, now)
        if other is not None and other.id != sub.id:
            raise Conflict("They have another active package; cancel that first")
    sub.ends_at = ends_at
    if sub.status in ("expired", "cancelled"):
        sub.status = "active"


def cancel(sub: Subscription) -> None:
    if sub.status not in ("active", "pending_payment"):
        raise PackageError(f"It's {sub.status} already")
    sub.status = "cancelled"


def end_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def grant(db: Session, user: User, gpu_min: float, *, reason: str = "", expires_at: datetime | None = None,
          granted_by: str | None = None) -> UsageGrant:
    """Extra minutes each day until it expires. With no expiry given it's a top-up for today (UTC)."""
    if gpu_min <= 0:
        raise PackageError("Grant at least a minute")
    now = utcnow()
    expires_at = expires_at or end_of_day(now)
    if expires_at <= now:
        raise PackageError("The grant has to end in the future")
    g = UsageGrant(user_id=user.id, gpu_min=float(gpu_min), reason=reason.strip()[:500], starts_at=now,
                   expires_at=expires_at, granted_by=granted_by)
    db.add(g)
    db.flush()
    return g


def live_grants(db: Session, user_id: str, now: datetime | None = None) -> list[UsageGrant]:
    now = now or utcnow()
    return list(db.scalars(select(UsageGrant).where(
        UsageGrant.user_id == user_id, UsageGrant.revoked_at.is_(None), UsageGrant.starts_at <= now,
        (UsageGrant.expires_at.is_(None)) | (UsageGrant.expires_at > now))))


def give_free(db: Session, user: User) -> Subscription | None:
    """A new account's starting package; nothing for admins, who aren't limited anyway."""
    if user.is_admin:
        return None
    pkg = free_package(db)
    if pkg is None:
        return None
    return assign(db, user, pkg, source="signup_default")


def activate(db: Session, sub: Subscription) -> None:
    """A pending_payment subscription once it's paid: it starts now, keeping the length it was given."""
    now = utcnow()
    length = sub.ends_at - sub.starts_at if sub.ends_at else None
    db.execute(update(Subscription).where(Subscription.user_id == sub.user_id, Subscription.status == "active",
                                          Subscription.id != sub.id)
               .values(status="cancelled", updated_at=now).execution_options(synchronize_session="fetch"))
    sub.status = "active"
    sub.starts_at = now
    sub.ends_at = now + length if length else None
