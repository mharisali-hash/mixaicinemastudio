"""Stems (contract v21): split a track or a video's sound into vocals and the rest, or four stems."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import audio_jobs as aj
from app import stems
from app.db import get_db
from app.security import CurrentUser, get_current_user, require_editor
from app.services import job_out

router = APIRouter(prefix="/audio", tags=["audio"])


class StemsIn(BaseModel):
    mode: Literal["two", "four"] = "two"
    version: str | None = None
    # also write out the words heard on the vocal stem (WhisperX)
    lyrics: bool = False


@router.post("/{item_id}/stems", status_code=202)
def split(item_id: str, body: StemsIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    item, g = stems.source(db, cur.workspace_id, item_id, body.version)
    job = stems.queue(db, item, g, body.mode, cur.id, lyrics=body.lyrics)
    db.commit()
    # the stems arrive as new Library items when the job finishes; GET lists them
    return {"job": job_out(job), "items": []}


@router.get("/{item_id}/stems")
def list_stems(item_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    aj._item(db, cur.workspace_id, item_id, "Track")
    return stems.splits_of(db, cur.workspace_id, item_id)
