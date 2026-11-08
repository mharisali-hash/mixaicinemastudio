"""Remix a song (contract v43): versions of one song, or a mashup / medley of two or three."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audio_analysis as aa
from app import remix
from app import remix_analysis as ra
from app.db import get_db
from app.security import CurrentUser, get_current_user, require_editor
from app.services import generation_file, job_out

router = APIRouter(prefix="/audio/remix", tags=["audio"])

Effect = Literal["slowed_reverb", "sped_up", "nightcore", "lofi", "bass_boost", "eight_d", "key_tempo"]


class RemixOptions(BaseModel):
    # new beat: style words for the new music; empty means one Auto picks
    style: str | None = Field(None, max_length=200)
    # new voice
    vocal: Literal["boy", "girl", "choir", "voice"] | None = None
    voice_id: str | None = None
    # key and tempo
    semitones: int = Field(0, ge=-6, le=6)
    tempo: float = Field(1.0, ge=0.7, le=1.4)
    # mashup: indexes into sources
    vocals_from: int = Field(0, ge=0, le=2)
    music_from: int = Field(1, ge=0, le=2)
    # an effect laid over the finished version (a slowed medley)
    then: Effect | None = None


class RemixStep(BaseModel):
    recipe: str = Field(max_length=40)
    options: RemixOptions = Field(default_factory=RemixOptions)


class RemixIn(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=remix.MAX_SOURCES)
    auto: bool = False
    recipes: list[RemixStep] = Field(default_factory=list, max_length=8)
    folder_id: str | None = None


def _steps(body: RemixIn) -> list[dict]:
    out = []
    for s in body.recipes:
        o = s.options.model_dump(exclude_defaults=True)
        o = {k: v for k, v in o.items() if v is not None}
        if s.recipe == "mashup":
            o.setdefault("vocals_from", 0)
            o.setdefault("music_from", 1)
        if s.recipe == "key_tempo" and not (o.get("semitones") or o.get("tempo")):
            raise HTTPException(422, "Key and tempo: move the key or the tempo first")
        out.append({"recipe": s.recipe, "options": o})
    return out


@router.get("/recipes")
def recipes(db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    avail = remix.availability(db, cur.workspace_id)
    return {"recipes": [{"id": r.id, "label": r.label, "hint": r.hint, "min_sources": r.min_sources,
                         "max_sources": r.max_sources, "available": avail[r.id] is None, "reason": avail[r.id]}
                        for r in remix.RECIPES.values()],
            "styles": list(remix.STYLES), "max_sources": remix.MAX_SOURCES}


@router.get("/analyse/{item_id}")
def analyse(item_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    [(item, g)] = remix.sources(db, cur.workspace_id, [item_id])
    try:
        got = ra.analyse(generation_file(g))
    except aa.AnalysisError as e:
        raise HTTPException(422, str(e)) from None
    return {"item_id": item.id, **got}


@router.post("", status_code=202)
def make(body: RemixIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    from app.api.library import generate_into

    srcs = remix.sources(db, cur.workspace_id, body.sources)
    avail = remix.availability(db, cur.workspace_id)
    plan = remix.auto_plan(len(srcs), avail) if body.auto else _steps(body)
    remix.check_plan(plan, len(srcs), avail)
    folder = generate_into(db, cur.workspace_id, body.folder_id, "audio") if body.folder_id else None
    items, jobs = remix.queue(db, cur.workspace_id, cur.id, srcs, plan, folder)
    db.commit()
    from app import library as lib

    return {"items": lib.items_out(db, items), "jobs": [job_out(j) for j in jobs]}


@router.get("/{item_id}")
def list_remixes(item_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    from app import audio_jobs as aj

    aj._item(db, cur.workspace_id, item_id, "Song")
    return remix.remixes_of(db, cur.workspace_id, item_id)
