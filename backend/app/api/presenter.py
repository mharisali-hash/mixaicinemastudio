"""Presenter Video: a character presenting a script to camera, scene by scene."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db import get_db
from app.limits import PROMPT_MAX
from app.models import PresenterVideo, utcnow
from app.presenter import service as svc
from app.security import CurrentUser, get_current_user, require_editor
from app.services import get_owned, job_out

router = APIRouter(prefix="/presenter", tags=["presenter"])

Lang = Field("en", min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")
Caption = Literal["classic", "pill", "highlight", "clean", "none"]


class SettingsIn(BaseModel):
    aspect: Literal["16:9", "9:16", "1:1"] | None = None
    caption_style: Caption | None = None
    background: str | None = Field(None, max_length=600, description="A preset key or your own words")
    lower_name: str | None = Field(None, max_length=120)
    lower_role: str | None = Field(None, max_length=120)
    title_card: bool | None = None


class CreateIn(BaseModel):
    character_id: str = Field(max_length=64)
    look_id: str | None = Field(None, max_length=64)
    title: str = Field("", max_length=200)
    language: str = Lang
    script: str = Field(min_length=1, max_length=PROMPT_MAX)
    settings: SettingsIn = SettingsIn()
    review: bool = Field(True, description="Stop after the frames for a look; off goes straight to a draft video")


class SceneIn(BaseModel):
    id: str | None = None
    text: str = Field(min_length=1, max_length=2000)
    background: str = Field("", max_length=600)


class PatchIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    look_id: str | None = Field(None, max_length=64)
    settings: SettingsIn | None = None
    # the whole list, in order: kept scenes keep their ids (and what was made for them), new ones get one
    scenes: list[SceneIn] | None = Field(None, max_length=svc.MAX_SCENES)


class RenderIn(BaseModel):
    quality: Literal["draft", "final"] = "draft"


def _pv(db: Session, cur: CurrentUser, pid: str) -> PresenterVideo:
    return get_owned(db, PresenterVideo, pid, cur.workspace_id, "Presenter video")


@router.get("")
def list_presenters(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(PresenterVideo).where(PresenterVideo.workspace_id == cur.workspace_id)
                      .order_by(PresenterVideo.updated_at.desc()).limit(200)).all()
    return [{"id": p.id, "title": p.title, "character_id": p.character_id, "scenes": len(p.scenes or []),
             "aspect": svc.settings_of(p)["aspect"], "outputs": p.outputs or {}, "updated_at": p.updated_at}
            for p in rows]


@router.post("", status_code=201)
def create_presenter(body: CreateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    pv = svc.create(db, cur, character_id=body.character_id, look_id=body.look_id, title=body.title,
                    language=body.language, script=body.script, settings=body.settings.model_dump())
    svc.queue(db, pv, "continue" if body.review else "render")
    db.commit()
    return svc.presenter_out(db, pv)


@router.get("/backgrounds")
def backgrounds(cur: CurrentUser = Depends(get_current_user)):
    return [{"key": k, "description": v} for k, v in svc.BACKGROUNDS.items()]


@router.get("/{pid}")
def get_presenter(pid: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return svc.presenter_out(db, _pv(db, cur, pid))


@router.patch("/{pid}")
def patch_presenter(pid: str, body: PatchIn, db: Session = Depends(get_db),
                    cur: CurrentUser = Depends(require_editor)):
    pv = _pv(db, cur, pid)
    svc.ensure_idle(db, pv)
    if body.title is not None:
        pv.title = body.title.strip()[:200] or pv.title
    if body.look_id is not None:
        from app import character_looks as cl

        c = svc.character_of(db, pv)
        if body.look_id and cl.finished_picture(db, c, body.look_id) is None:
            raise HTTPException(422, "That look isn't this character's, or its picture isn't ready yet")
        pv.look_id = body.look_id or None
    if body.settings is not None:
        pv.settings = {**svc.settings_of(pv), **body.settings.model_dump(exclude_none=True)}
    if body.scenes is not None:
        old = {s["id"]: s for s in pv.scenes or []}
        scenes = []
        for s in body.scenes:
            prev = old.get(s.id or "")
            # an edited scene keeps its parts; their fingerprints decide what has to be made again
            sc = {**prev, "text": s.text.strip(), "background": s.background.strip()} if prev else \
                svc.new_scene(s.text, s.background)
            scenes.append(sc)
        if not scenes:
            raise HTTPException(422, "A presenter video needs at least one scene")
        svc.check_script(scenes, cur)
        pv.scenes = scenes
        flag_modified(pv, "scenes")
    # anything may have changed: every stage looks again, and only changed scenes are remade
    pv.stages = {k: {"status": "pending"} for k in svc.STAGE_KEYS}
    pv.updated_at = utcnow()
    db.commit()
    return svc.presenter_out(db, pv)


@router.delete("/{pid}", status_code=204)
def delete_presenter(pid: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    pv = _pv(db, cur, pid)
    svc.ensure_idle(db, pv)
    db.delete(pv)
    db.commit()


@router.post("/{pid}/continue", status_code=202)
def continue_presenter(pid: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Voice and frames (stopping to let you check the frames), or carry on past that review."""
    pv = _pv(db, cur, pid)
    job = svc.queue(db, pv, "continue")
    db.commit()
    return job_out(job)


@router.post("/{pid}/render", status_code=202)
def render_presenter(pid: str, body: RenderIn, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    """Every scene filmed (only what changed is made again) and the video cut, at draft or final quality."""
    pv = _pv(db, cur, pid)
    job = svc.queue(db, pv, "render", body.quality)
    db.commit()
    return job_out(job)


@router.post("/{pid}/scenes/{sid}/redo", status_code=202)
def redo_scene(pid: str, sid: str, what: Literal["frame", "take"] = "frame", db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    """Draw a scene's frame (or film its take) again: its fingerprint is cleared, so the next run makes it."""
    pv = _pv(db, cur, pid)
    svc.ensure_idle(db, pv)
    scenes = [dict(s) for s in pv.scenes or []]
    sc = next((s for s in scenes if s["id"] == sid), None)
    if sc is None:
        raise HTTPException(404, "Scene not found")
    if what == "frame":
        sc["frame"] = {}
        sc["take"] = {}
    else:
        sc["take"] = {}
    pv.scenes = scenes
    pv.stages = {k: {"status": "pending"} for k in svc.STAGE_KEYS}
    job = svc.queue(db, pv, "continue" if what == "frame" else "render")
    db.commit()
    return job_out(job)
