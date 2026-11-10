"""GET /models (contract v6): what the pickers offer, with live availability."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import estimate as est
from app import models_catalog as mc
from app import rights
from app import upscale
from app.config import get_settings
from app.db import get_db
from app.models import Generation
from app.security import CurrentUser, get_current_user

router = APIRouter(tags=["models"])


def measured(db: Session, workspace_id: str) -> dict[str, float]:
    rows = db.scalars(select(Generation.params).where(
        Generation.workspace_id == workspace_id, Generation.status.in_(("ready", "approved")),
        Generation.kind.in_(("portrait", "establishing", "keyframe_start", "keyframe_end", "image", "take", "video",
                             "song", "music", "sfx")),
    ).order_by(Generation.created_at.desc()).limit(300)).all()
    return mc.measured_seconds(list(rows))


@router.get("/models")
async def list_models(type: Literal["image", "edit", "video", "upscale", "audio", "model3d"] | None = None,
                      db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    s = get_settings()
    info, error = (None, None) if s.gen_driver == "mock" else await upscale.fetch_object_info()
    rows = mc.catalog(type, info, driver=s.gen_driver, error=error, measured=measured(db, cur.workspace_id))
    r = rights.effective(db, cur.user)
    # what this person's plan doesn't have isn't offered at all (Auto stays: it only picks from what they have)
    return rows if r.admin else [row for row in rows if row.get("auto") or r.model_ok(row["id"])]


@router.get("/estimate")
def estimate(kind: Literal["image", "video", "take", "upscale", "audio", "speech", "model3d"] = "image",
             model: str | None = Query(None, max_length=60),
             speed: str | None = Query(None, max_length=30, description="A model's speed id; for model3d the "
                                                                           "quality, draft or final"),
             count: int = Query(1, ge=1, le=25),
             duration_s: float | None = Query(None, gt=0, le=1200), width: int | None = Query(None, ge=64, le=8192),
             height: int | None = Query(None, ge=64, le=8192), chars: int | None = Query(None, ge=0, le=20000),
             better_back: bool = Query(False, description="model3d: add 3D Studio's side and back views, made "
                                                          "first with the edit model"),
             db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    if kind == "speech":
        return est.speech_estimate(model=model, count=count, chars=chars, duration_s=duration_s)
    return est.estimate(db, cur.workspace_id, kind=kind, model=model, speed=speed, count=count,
                        duration_s=duration_s, width=width, height=height, better_back=better_back)
