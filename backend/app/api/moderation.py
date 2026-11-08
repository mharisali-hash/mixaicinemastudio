"""Moderation screens (contract v42): the review queue, people, and the log. Admins and managers; everyone else
gets a 404. Every route checks the item or person is within the moderator's reach (app.moderation.reach)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import moderation as mod
from app import thumbs
from app.api.media import resolve_media_path
from app.config import get_settings
from app.db import get_db, release
from app.models import AdminAudit, User
from app.security import CurrentUser, require_moderator, session_only

router = APIRouter(prefix="/moderation", tags=["moderation"])


class IdsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class RemoveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    warn: bool = False
    email: bool = False


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    item_id: str | None = None
    email: bool = False


class UnblockIn(BaseModel):
    reason: str = Field(default="", max_length=1000)
    email: bool = False


def _naive(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=None) if at is not None and at.tzinfo is not None else at


@router.get("/items")
@session_only
def items(user: str | None = Query(None, max_length=36), kind: str | None = Query(None, pattern="^(image|video|audio)$"),
          since: datetime | None = None, until: datetime | None = None, before: datetime | None = None,
          state: str = Query("queue", pattern="^(queue|reviewed|removed|all)$"), q: str = Query("", max_length=200),
          limit: int = Query(60, ge=1, le=200), db: Session = Depends(get_db),
          cur: CurrentUser = Depends(require_moderator)):
    rows = mod.list_items(db, cur, user_id=user, kind=kind, since=_naive(since), until=_naive(until), state=state,
                          q=q, limit=limit, before=_naive(before))
    out = [mod.item_out(db, m, u) for m, u in rows]
    return {"items": out, "next": out[-1]["created_at"] if len(out) == limit else None}


@router.post("/items/reviewed")
@session_only
def reviewed(body: IdsIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_moderator)):
    n = mod.mark_reviewed(db, cur, body.ids)
    db.commit()
    return {"reviewed": n}


@router.post("/items/{item_id}/remove")
@session_only
def remove(item_id: str, body: RemoveIn, db: Session = Depends(get_db),
           cur: CurrentUser = Depends(require_moderator)):
    item, owner = mod.item_in_reach(db, cur, item_id)
    mod.remove(db, cur, item, owner, body.reason.strip(), warn=body.warn, email=body.email)
    db.commit()
    return mod.item_out(db, item, owner)


@router.post("/items/{item_id}/restore")
@session_only
def restore(item_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_moderator)):
    item, owner = mod.item_in_reach(db, cur, item_id)
    mod.restore(db, cur, item)
    db.commit()
    return mod.item_out(db, item, owner)


def _file(db: Session, cur: CurrentUser, item_id: str):
    item, _ = mod.item_in_reach(db, cur, item_id)
    g = mod.current_generation(db, item)
    path = resolve_media_path(g.file_path or "", item.workspace_id, get_settings().data_dir) if g else None
    if path is None:
        raise HTTPException(404, "No file")
    return g, path


@router.get("/items/{item_id}/thumb")
def item_thumb(item_id: str, w: int = Query(thumbs.DEFAULT), db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_moderator)):
    if w not in thumbs.SIZES:
        raise HTTPException(422, f"w must be one of {', '.join(map(str, thumbs.SIZES))}")
    g, src = _file(db, cur, item_id)
    dest = thumbs.ensure(src, g.media_type, w)
    if dest is None:
        raise HTTPException(404, "No preview for this file")
    release(db)
    return FileResponse(dest, media_type="image/png" if dest.suffix == ".png" else "image/webp",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/items/{item_id}/file")
def item_file(item_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_moderator)):
    g, path = _file(db, cur, item_id)
    release(db)
    return FileResponse(path, media_type=g.media_type or None, headers={"Cache-Control": "private, no-store"})


@router.get("/users")
@session_only
def users(q: str = Query("", max_length=200), status: str = Query("all", pattern="^(all|warned|blocked)$"),
          limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
          cur: CurrentUser = Depends(require_moderator)):
    stmt = select(User).where(*mod.reach(cur))
    needle = q.strip().lower()
    if needle:
        stmt = stmt.where(or_(User.email.ilike(f"%{needle}%"), User.display_name.ilike(f"%{needle}%")))
    if status == "blocked":
        stmt = stmt.where(User.blocked_at.is_not(None))
    rows = list(db.scalars(stmt.order_by(User.blocked_at.is_(None), User.created_at.desc()).limit(limit)))
    counts = mod.warnings_since(db, [u.id for u in rows])
    if status == "warned":
        rows = [u for u in rows if counts.get(u.id)]
    return {"warn_limit": mod.warn_limit(),
            "users": [{"id": u.id, "email": u.email, "name": u.display_name, "is_active": bool(u.is_active),
                       "warnings": int(counts.get(u.id, 0)), "blocked_at": u.blocked_at,
                       "blocked_reason": u.blocked_reason or ""} for u in rows]}


@router.post("/users/{user_id}/warn", status_code=201)
@session_only
def warn(user_id: str, body: ReasonIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_moderator)):
    user = mod.in_reach(db, cur, user_id)
    item = None
    if body.item_id:
        item, owner = mod.item_in_reach(db, cur, body.item_id)
        if owner.id != user.id:
            raise HTTPException(422, "That item isn't theirs")
    mod.warn(db, cur, user, body.reason.strip(), item=item, email=body.email)
    db.commit()
    return {"warnings": mod.warnings_since(db, [user.id]).get(user.id, 0), "warn_limit": mod.warn_limit()}


@router.post("/users/{user_id}/block")
@session_only
def block(user_id: str, body: ReasonIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_moderator)):
    user = mod.in_reach(db, cur, user_id)
    mod.block(db, cur, user, body.reason.strip(), email=body.email)
    db.commit()
    return {"id": user.id, "blocked_at": user.blocked_at, "blocked_reason": user.blocked_reason}


@router.post("/users/{user_id}/unblock")
@session_only
def unblock(user_id: str, body: UnblockIn, db: Session = Depends(get_db),
            cur: CurrentUser = Depends(require_moderator)):
    user = mod.in_reach(db, cur, user_id)
    mod.unblock(db, cur, user, body.reason.strip(), email=body.email)
    db.commit()
    return {"id": user.id, "blocked_at": None, "blocked_reason": ""}


@router.get("/log")
@session_only
def log(limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db),
        cur: CurrentUser = Depends(require_moderator)):
    stmt = select(AdminAudit).where(AdminAudit.action.like("moderation_%"))
    if not cur.user.is_admin:
        # a manager sees what was done to the people in their reach, and their own sweeps of the queue
        people = select(User.id).where(*mod.reach(cur))
        stmt = stmt.where(or_(AdminAudit.target_id.in_(people), AdminAudit.actor_user_id == cur.user.id))
    rows = db.scalars(stmt.order_by(AdminAudit.created_at.desc()).limit(limit))
    return [{"id": a.id, "at": a.created_at, "actor": a.actor_email, "action": a.action, "target_type": a.target_type,
             "target_id": a.target_id, "detail": a.detail} for a in rows]
