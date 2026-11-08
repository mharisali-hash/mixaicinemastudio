"""Moderation (contract v42): managers and admins look over what people make, take down what slipped past
app.content_policy, tell the person why, and put an account on hold when warnings don't help.

Who can be acted on: never an admin. A manager also never acts on another manager, and a team manager only on
the people an admin gave them (manager_member). Implant Preview stays out of all of it: those are Generation
rows filed outside the workspace folders, never MediaItems, so nothing here can list them.
"""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app import audit, mailer
from app.config import get_settings
from app.models import Generation, Job, ManagerMember, MediaItem, User, UserNotice, Workspace, utcnow
from app.security import CurrentUser

WARNING, REMOVAL, BLOCK, UNBLOCK = "warning", "removal", "block", "unblock"
WARN_WINDOW = timedelta(days=90)


# ---------------------------------------------------------------- who is in reach

def is_staff(user: User) -> bool:
    return bool(user.is_admin) or user.staff_role == "manager"


def reach(cur: CurrentUser):
    """SQL conditions on User for the accounts this moderator may see and act on."""
    me = cur.user
    conds = [User.is_admin.is_(False), User.id != me.id]
    if me.is_admin:
        return conds
    conds.append(User.staff_role.is_(None))
    if (me.manager_scope or "site") == "team":
        conds.append(User.id.in_(select(ManagerMember.user_id).where(ManagerMember.manager_user_id == me.id)))
    return conds


def in_reach(db: Session, cur: CurrentUser, user_id: str | None) -> User:
    """The account, or the same 404 for missing and out-of-reach so a team manager can't probe ids."""
    user = db.scalars(select(User).where(User.id == (user_id or ""), *reach(cur))).first()
    if user is None:
        raise HTTPException(404, "User not found")
    return user


def _owned_items(cur: CurrentUser):
    # a MediaItem's owner is its workspace's owner (one workspace per person for now)
    return (select(MediaItem, User).join(Workspace, Workspace.id == MediaItem.workspace_id)
            .join(User, User.id == Workspace.owner_user_id).where(*reach(cur)))


def item_in_reach(db: Session, cur: CurrentUser, item_id: str) -> tuple[MediaItem, User]:
    row = db.execute(_owned_items(cur).where(MediaItem.id == item_id)).first()
    if row is None:
        raise HTTPException(404, "Item not found")
    return row[0], row[1]


# ---------------------------------------------------------------- the review list

STATES = ("queue", "reviewed", "removed", "all")


def list_items(db: Session, cur: CurrentUser, *, user_id: str | None = None, kind: str | None = None,
               since=None, until=None, state: str = "queue", q: str = "", limit: int = 60,
               before=None) -> list[tuple[MediaItem, User]]:
    stmt = _owned_items(cur).where(MediaItem.kind.in_(("image", "video", "audio")))
    if state == "queue":
        # uploads too: a picture brought in from outside can break the rules as easily as one made here
        stmt = stmt.where(MediaItem.reviewed_at.is_(None), MediaItem.removed_at.is_(None))
    elif state == "reviewed":
        stmt = stmt.where(MediaItem.reviewed_at.is_not(None), MediaItem.removed_at.is_(None))
    elif state == "removed":
        stmt = stmt.where(MediaItem.removed_at.is_not(None))
    if user_id:
        stmt = stmt.where(User.id == user_id)
    if kind:
        stmt = stmt.where(MediaItem.kind == kind)
    if since:
        stmt = stmt.where(MediaItem.created_at >= since)
    if until:
        stmt = stmt.where(MediaItem.created_at < until)
    if before:
        stmt = stmt.where(MediaItem.created_at < before)
    needle = q.strip().lower()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(func.lower(MediaItem.title).like(like), func.lower(User.email).like(like),
                              func.lower(User.display_name).like(like)))
    stmt = stmt.order_by(MediaItem.created_at.desc()).limit(max(1, min(200, limit)))
    return [(m, u) for m, u in db.execute(stmt).all()]


def current_generation(db: Session, item: MediaItem) -> Generation | None:
    return db.get(Generation, item.generation_id) if item.generation_id else None


def item_out(db: Session, item: MediaItem, owner: User) -> dict:
    g = current_generation(db, item)
    params = (g.params or {}) if g else {}
    base = f"/api/moderation/items/{item.id}"
    return {"id": item.id, "kind": item.kind, "origin": item.origin, "title": item.title,
            "prompt": (params.get("user_prompt") or params.get("prompt") or "")[:600],
            "media_type": g.media_type if g else None, "width": item.width, "height": item.height,
            "duration_s": item.duration_s, "created_at": item.created_at,
            "owner": {"id": owner.id, "email": owner.email, "name": owner.display_name},
            "reviewed_at": item.reviewed_at, "removed_at": item.removed_at, "removed_reason": item.removed_reason,
            "thumb_url": f"{base}/thumb" if g and g.file_path else None,
            "file_url": f"{base}/file" if g and g.file_path else None}


# ---------------------------------------------------------------- actions

def mark_reviewed(db: Session, cur: CurrentUser, ids: list[str]) -> int:
    allowed = [m.id for m, _ in db.execute(_owned_items(cur).where(MediaItem.id.in_(ids or [""]))).all()]
    if not allowed:
        return 0
    db.execute(update(MediaItem).where(MediaItem.id.in_(allowed), MediaItem.reviewed_at.is_(None))
               .values(reviewed_at=utcnow(), reviewed_by=cur.user.id).execution_options(synchronize_session=False))
    audit.record(db, cur.user, "moderation_reviewed", "media", None, ids=allowed[:200], count=len(allowed))
    return len(allowed)


def remove(db: Session, cur: CurrentUser, item: MediaItem, owner: User, reason: str, *, warn: bool,
           email: bool) -> UserNotice | None:
    if item.removed_at is not None:
        raise HTTPException(409, "It's removed already")
    now = utcnow()
    item.removed_at, item.removed_by, item.removed_reason = now, cur.user.id, reason
    item.reviewed_at, item.reviewed_by = item.reviewed_at or now, item.reviewed_by or cur.user.id
    audit.record(db, cur.user, "moderation_remove", "media", item.id, owner=owner.id, reason=reason)
    # the owner always hears that something of theirs went; with warn it also counts towards a hold
    return notify(db, cur, owner, WARNING if warn else REMOVAL, reason, item=item, email=email)


def restore(db: Session, cur: CurrentUser, item: MediaItem) -> None:
    if item.removed_at is None:
        raise HTTPException(409, "It isn't removed")
    item.removed_at = item.removed_by = None
    item.removed_reason = ""
    audit.record(db, cur.user, "moderation_restore", "media", item.id)


def warn(db: Session, cur: CurrentUser, user: User, reason: str, *, item: MediaItem | None = None,
         email: bool = False) -> UserNotice:
    return notify(db, cur, user, WARNING, reason, item=item, email=email)


def block(db: Session, cur: CurrentUser, user: User, reason: str, *, email: bool = False) -> UserNotice:
    if user.blocked_at is not None:
        raise HTTPException(409, "That account is on hold already")
    user.blocked_at, user.blocked_reason, user.blocked_by = utcnow(), reason, cur.user.id
    # what's waiting doesn't get made; what's running finishes (rights.check_render stops anything it queues)
    jobs = db.scalars(select(Job).where(Job.user_id == user.id, Job.status == "queued")).all()
    for job in jobs:
        job.status, job.message, job.finished_at = "cancelled", "Cancelled: the account is on hold", utcnow()
        if job.generation_id:
            g = db.get(Generation, job.generation_id)
            if g is not None and g.status in ("queued", "generating"):
                g.status = "failed"
    return notify(db, cur, user, BLOCK, reason, email=email, cancelled=len(jobs))


def unblock(db: Session, cur: CurrentUser, user: User, reason: str = "", *, email: bool = False) -> UserNotice:
    if user.blocked_at is None:
        raise HTTPException(409, "That account isn't on hold")
    user.blocked_at, user.blocked_reason, user.blocked_by = None, "", None
    return notify(db, cur, user, UNBLOCK, reason or "Your account is open again.", email=email)


def notify(db: Session, cur: CurrentUser, user: User, kind: str, reason: str, *, item: MediaItem | None = None,
           email: bool = False, **detail) -> UserNotice:
    n = UserNotice(user_id=user.id, kind=kind, reason=reason, item_type="media" if item else "",
                   item_id=item.id if item else None, created_by=cur.user.id)
    db.add(n)
    if kind != REMOVAL:
        audit.record(db, cur.user, f"moderation_{kind}", "user", user.id, reason=reason,
                     item=item.id if item else None, email=email, **detail)
    if email and user.email:
        mailer.deliver(_mail(user, kind, reason))
        n.emailed = True
    return n


SUBJECTS = {WARNING: "A warning about your account", REMOVAL: "We removed one of your items",
            BLOCK: "Your account is on hold", UNBLOCK: "Your account is open again"}


def _mail(user: User, kind: str, reason: str):
    lead = {WARNING: "A moderator has sent you a warning.", REMOVAL: "A moderator removed one of your items.",
            BLOCK: "A moderator has put your account on hold. You can still sign in, look at and download your "
                   "work, but you can't make anything new for now.",
            UNBLOCK: "Your account is open again, and you can carry on creating."}[kind]
    return mailer.compose(user.email, SUBJECTS[kind], SUBJECTS[kind], [lead, f"Reason: {reason}"],
                          button=("Open the studio", mailer.link("/")))


def warnings_since(db: Session, user_ids: list[str]) -> dict[str, int]:
    since = utcnow() - WARN_WINDOW
    if not user_ids:
        return {}
    return dict(db.execute(select(UserNotice.user_id, func.count()).where(
        UserNotice.user_id.in_(user_ids), UserNotice.kind == WARNING, UserNotice.created_at >= since)
        .group_by(UserNotice.user_id)).all())


def warn_limit() -> int:
    return get_settings().moderation_warn_limit


# ---------------------------------------------------------------- for the person themselves

def open_notices(db: Session, user: User) -> list[UserNotice]:
    rows = list(db.scalars(select(UserNotice).where(UserNotice.user_id == user.id,
                                                    UserNotice.acknowledged_at.is_(None),
                                                    UserNotice.kind != BLOCK)
                           .order_by(UserNotice.created_at.desc()).limit(10)))
    return rows


def acknowledge(db: Session, user: User, notice_id: str) -> None:
    n = db.get(UserNotice, notice_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "Notice not found")
    if n.acknowledged_at is None:
        n.acknowledged_at = utcnow()


# ---------------------------------------------------------------- hiding removed items from their owner

def removed_generation(db: Session, g: Generation) -> bool:
    """A version of a removed Library item: its files aren't served to the owner any more."""
    if g.target_type != "media" or not g.target_id:
        return False
    return db.scalar(select(exists().where(MediaItem.id == g.target_id, MediaItem.removed_at.is_not(None)))) or False


def removed_path(db: Session, workspace_id: str, rel: str) -> bool:
    # cheap first: most workspaces have nothing removed, and then no file lookup is needed at all
    if not db.scalar(select(exists().where(MediaItem.workspace_id == workspace_id,
                                           MediaItem.removed_at.is_not(None)))):
        return False
    return db.scalar(select(exists().where(and_(
        Generation.workspace_id == workspace_id, Generation.file_path == rel, Generation.target_type == "media",
        MediaItem.id == Generation.target_id, MediaItem.removed_at.is_not(None))))) or False
