"""What keeps Implant Preview's photos and results out of the rest of the app."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_policy import ids_in as _ids_in
from app.models import Generation
from app.security import CurrentUser, get_current_user

TARGETS = ("implant_photo", "implant_result")


def is_private(g: Generation | None) -> bool:
    return g is not None and g.target_type in TARGETS


def private_ids():
    """A subquery of every implant generation id, for listings that must leave them out."""
    return select(Generation.id).where(Generation.target_type.in_(TARGETS))


def refuses(db: Session, gen: Generation) -> bool:
    """True when a job outside the module names an implant photo or result anywhere in its params. Checked by
    the worker before any driver runs, so no other studio, the API or MCP can feed one into a render."""
    if gen.target_type == "implant_result":
        return False
    ids: set[str] = set()
    _ids_in(gen.params or {}, ids)
    ids.discard(gen.id)
    if not ids:
        return False
    return db.scalar(select(Generation.id).where(Generation.id.in_(ids), Generation.target_type.in_(TARGETS))
                     .limit(1)) is not None


def has_access(cur: CurrentUser) -> bool:
    return bool(cur.user.is_admin or cur.user.implant_access)


def require_implant(cur: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    # 404, as for the admin routes: the area doesn't exist for anyone else
    if not has_access(cur):
        raise HTTPException(404, "Not found")
    return cur
