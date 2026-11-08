"""The signed-in user's own package and GPU meter (contract v39). Tokens may read it too."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import moderation as mod
from app import packages as pk
from app.db import get_db
from app.quota import usage
from app.security import CurrentUser, get_current_user

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/usage")
def my_usage(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    if pk.expire_due(db):
        db.commit()
    return usage(db, cur.user)


@router.get("/notices")
def my_notices(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    """What a moderator told this person and they haven't dismissed yet; a hold is in /auth/me, not here."""
    return [{"id": n.id, "kind": n.kind, "reason": n.reason, "item_id": n.item_id, "at": n.created_at}
            for n in mod.open_notices(db, cur.user)]


@router.post("/notices/{notice_id}/ack", status_code=204)
def acknowledge(notice_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    mod.acknowledge(db, cur.user, notice_id)
    db.commit()
