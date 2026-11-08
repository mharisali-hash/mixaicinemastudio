"""Admin › Rights (contract v42): what's released, the package grid, and one person's overrides and staff role.
Admins only; managers get the same 404 as everyone else."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app import models_catalog as mc
from app import packages as pk
from app import rights
from app.db import get_db
from app.models import ManagerMember, Package, User
from app.security import CurrentUser, require_admin, session_only

router = APIRouter(prefix="/admin", tags=["admin"])


class ReleaseIn(BaseModel):
    kind: Literal["feature", "model"]
    key: str = Field(min_length=1, max_length=120)


class OverridesIn(BaseModel):
    # {key: true | false | null}; null (or leaving it out) follows the plan
    features: dict[str, bool | None] = Field(default_factory=dict)
    models: dict[str, bool | None] = Field(default_factory=dict)


class StaffIn(BaseModel):
    staff_role: Literal["manager"] | None = None
    manager_scope: Literal["site", "team"] = "site"
    team: list[str] = Field(default_factory=list, max_length=2000)


def _user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


@router.get("/rights")
@session_only
def rights_overview(db: Session = Depends(get_db), _=Depends(require_admin)):
    rel = rights.released(db)
    features = [{"key": k, "label": label, "released": k in rel[rights.FEATURE], "own_switch": k in rights.OWN_SWITCH}
                for k, label in pk.FEATURES.items()]
    # licence-gated models don't exist as far as anyone outside the backend can tell
    models = [{"key": m.id, "label": m.label, "type": m.type, "released": m.id in rel[rights.MODEL]}
              for m in mc.MODELS.values() if not mc.hidden(m)]
    packages = [{"id": p.id, "name": p.name, "kind": p.kind, "custom": p.custom_for_user_id is not None,
                 "features": dict(p.features or {}), "models": list(p.models) if p.models is not None else None}
                for p in db.scalars(select(Package).where(Package.active.is_(True)).order_by(Package.sort, Package.name))]
    return {"features": features, "models": models, "packages": packages}


@router.post("/rights/release")
@session_only
def release(body: ReleaseIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    if not rights.known(body.kind, body.key):
        raise HTTPException(422, f"Unknown {body.kind} '{body.key}'")
    if rights.release(db, body.kind, body.key, cur.user):
        audit.record(db, cur.user, "right_release", body.kind, None, key=body.key)
    db.commit()
    return {"kind": body.kind, "key": body.key, "released": True}


@router.post("/rights/withdraw")
@session_only
def withdraw(body: ReleaseIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    if not rights.known(body.kind, body.key):
        raise HTTPException(422, f"Unknown {body.kind} '{body.key}'")
    if rights.withdraw(db, body.kind, body.key):
        audit.record(db, cur.user, "right_withdraw", body.kind, None, key=body.key)
    db.commit()
    return {"kind": body.kind, "key": body.key, "released": False}


def _user_rights(db: Session, user: User) -> dict:
    r = rights.effective(db, user)
    feats, models = rights.overrides(user)
    team = list(db.scalars(select(ManagerMember.user_id).where(ManagerMember.manager_user_id == user.id)))
    return {"user_id": user.id, "admin": bool(user.is_admin), "package": r.package_name if not r.admin else "Unlimited",
            "overrides": {"features": feats, "models": models},
            "features": {k: r.allows(k) for k in pk.FEATURES},
            "models": r.models(),
            "staff_role": user.staff_role, "manager_scope": user.manager_scope or "site", "team": team}


@router.get("/users/{user_id}/rights")
@session_only
def user_rights(user_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    return _user_rights(db, _user(db, user_id))


@router.put("/users/{user_id}/rights")
@session_only
def set_user_rights(user_id: str, body: OverridesIn, db: Session = Depends(get_db),
                    cur: CurrentUser = Depends(require_admin)):
    user = _user(db, user_id)
    clean = rights.clean_overrides(body.model_dump())
    before = rights.overrides(user)
    user.rights = clean
    audit.record(db, cur.user, "rights_update", "user", user.id,
                 before={"features": before[0], "models": before[1]}, after=clean)
    db.commit()
    return _user_rights(db, user)


@router.put("/users/{user_id}/staff")
@session_only
def set_staff(user_id: str, body: StaffIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    user = _user(db, user_id)
    if user.is_admin and body.staff_role:
        raise HTTPException(422, "Admins can do everything a manager can already")
    team = [] if body.staff_role is None or body.manager_scope == "site" else list(dict.fromkeys(body.team))
    if user.id in team:
        raise HTTPException(422, "A manager can't look after their own account")
    if team:
        found = set(db.scalars(select(User.id).where(User.id.in_(team))))
        missing = [t for t in team if t not in found]
        if missing:
            raise HTTPException(422, f"Unknown user '{missing[0]}'")
    user.staff_role = body.staff_role
    user.manager_scope = body.manager_scope
    db.execute(delete(ManagerMember).where(ManagerMember.manager_user_id == user.id))
    db.add_all(ManagerMember(manager_user_id=user.id, user_id=t) for t in team)
    audit.record(db, cur.user, "staff_update", "user", user.id, staff_role=body.staff_role,
                 manager_scope=body.manager_scope, team=len(team))
    db.commit()
    return _user_rights(db, user)
