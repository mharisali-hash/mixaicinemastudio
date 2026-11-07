"""Poetry Shorts (contract v33): a poem, sung by ACE-Step, timed line by line and cut to scenes."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.limits import PROMPT_MAX
from app import visibility as vis
from app import models_catalog as mc
from app import song_voice
from app.db import get_db
from app.models import Character, Generation, PoetryShort
from app.poetry import service as svc
from app.poetry import timing as tm
from app.schemas import JobOut
from app.security import CurrentUser, get_current_user, require_editor
from app.services import get_owned, job_out

router = APIRouter(prefix="/poetry", tags=["poetry"])

Lang = Field("ur", min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")


class CreateIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "topic": "falling apart slowly, like dust in the wind", "language": "ur", "mood": "melancholy, longing",
        "style": "slow ghazal, harmonium, tabla, soft strings", "vocal": "female", "length_s": 60,
        "character": "a woman in a dusty-rose hooded robe, seen from behind",
        "setting": "an old Mughal city, golden hour into lantern-lit night", "aspect": "9:16"}]})

    title: str = Field("", max_length=200)
    topic: str = Field("", max_length=PROMPT_MAX, description="What the poem is about; or give your own poem")
    poem: str = Field("", max_length=4000, description="Your own poem; [verse] / [chorus] markers are optional")
    language: str = Lang
    mood: str = Field("", max_length=PROMPT_MAX)
    style: str = Field("", max_length=PROMPT_MAX, description="Music style tags for the singer")
    # boy / girl / choir: sung by the model, then changed into a child's voice (app.song_voice)
    vocal: Literal["female", "male", "boy", "girl", "choir"] = "female"
    length_s: float = Field(60, ge=30, le=90)
    character_id: str | None = Field(None, max_length=64, description="A Character Studio character")
    character: str = Field("", max_length=PROMPT_MAX, description="Or a short description of the person")
    setting: str = Field("", max_length=PROMPT_MAX)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    quality: Literal["draft", "final"] = "draft"
    image_model: str = Field("krea2_turbo", max_length=60)
    title_card: bool = False
    review: bool = Field(True, description="Stop for an OK after the poem, song, timing and scenes")
    whisper: bool = True

    @model_validator(mode="after")
    def something_to_sing(self):
        if not self.topic.strip() and not self.poem.strip():
            raise ValueError("give a topic or your own poem")
        return self


class PoemPatch(BaseModel):
    lyrics: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(None, max_length=200)


class TimingRow(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class TimingsPatch(BaseModel):
    lines: list[TimingRow] = Field(min_length=1, max_length=200)


class ScenePatch(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    image_prompt: str | None = Field(None, min_length=1, max_length=PROMPT_MAX)
    motion_prompt: str | None = Field(None, min_length=1, max_length=PROMPT_MAX)


class ScenesPatch(BaseModel):
    scenes: list[ScenePatch] = Field(min_length=1, max_length=20)


class RegenerateIn(BaseModel):
    what: Literal["keyframe", "clip"] = "keyframe"
    image_prompt: str | None = Field(None, min_length=1, max_length=PROMPT_MAX)
    motion_prompt: str | None = Field(None, min_length=1, max_length=PROMPT_MAX)


def _short(db: Session, cur: CurrentUser, short_id: str) -> PoetryShort:
    return get_owned(db, PoetryShort, short_id, cur.workspace_id, "Poetry Short")


def _out(db: Session, ps: PoetryShort) -> dict:
    return svc.short_out(db, ps)


@router.post("", status_code=201, summary="Make a Poetry Short",
             description="Starts the stage job straight away (poem, song, timing, scenes, render). With review on it "
                         "stops after each of the first four for POST /continue; with review off it runs to the end.")
def create_short(body: CreateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    if body.character_id:
        c = db.get(Character, body.character_id)
        if c is None or c.workspace_id != cur.workspace_id:
            raise HTTPException(422, "That character isn't in this workspace")
    mc.get(body.image_model, "image")  # 422 when it isn't an image model or isn't installed
    if body.vocal in song_voice.VOCALS and not song_voice.enabled():
        raise HTTPException(422, "Children's voices aren't set up on this server yet")
    ps = svc.create(db, cur, body.model_dump())
    svc.queue(db, ps)
    db.commit()
    return _out(db, ps)


@router.get("", summary="List Poetry Shorts")
def list_shorts(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(PoetryShort).where(PoetryShort.workspace_id == cur.workspace_id,
                                                *vis.where(PoetryShort, vis.wanted(cur)))
                      .order_by(PoetryShort.updated_at.desc()).limit(200)).all()
    return [svc.summary_out(db, ps) for ps in rows]


@router.get("/{short_id}")
def get_short(short_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return _out(db, _short(db, cur, short_id))


@router.delete("/{short_id}", status_code=204)
def delete_short(short_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    # the song and the finished videos are Library items and stay there
    db.delete(ps)
    db.commit()
    return Response(status_code=204)


@router.patch("/{short_id}/poem", summary="Edit the poem",
              description="Replaces the lines. After the song was made this sends the Short back to the song: the "
                          "song, timings and scenes are made again for the new words.")
def patch_poem(short_id: str, body: PoemPatch, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    lines = svc.parse_lines(body.lyrics)
    if not lines:
        raise HTTPException(422, "The poem has no lines to sing")
    if body.title is not None:
        ps.title = body.title.strip()
    ps.poem = {**(ps.poem or {}), "lyrics": body.lyrics.strip(), "edited": True}
    ps.lines = lines
    if svc.stage(ps, "poem").get("status") not in ("review", "pending", "failed"):
        svc.reset_from(ps, "song")
        ps.song = {k: v for k, v in (ps.song or {}).items() if k == "media_id"}
        ps.timing, ps.scenes = {}, []
    db.commit()
    return _out(db, ps)


@router.patch("/{short_id}/timings", summary="Edit the line timings",
              description="One {start, end} per line, in order. Only the cut is redone (no GPU).")
def patch_timings(short_id: str, body: TimingsPatch, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    if not svc.timed(ps):
        raise HTTPException(409, "The lines haven't been timed yet")
    if len(body.lines) != len(ps.lines):
        raise HTTPException(422, f"Give one timing per line ({len(ps.lines)} lines)")
    try:
        rows = tm.check_edit([r.model_dump() for r in body.lines], svc.song_duration(ps))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    ps.lines = [{**ln, **r} for ln, r in zip(ps.lines, rows)]
    ps.timing = {**(ps.timing or {}), "edited": True}
    if svc.done(ps, "render"):
        svc.reset_from(ps, "render")
    db.commit()
    return _out(db, ps)


def _scene(ps: PoetryShort, scene_id: str) -> dict:
    sc = next((s for s in ps.scenes or [] if s["id"] == scene_id), None)
    if sc is None:
        raise HTTPException(404, "Scene not found")
    return dict(sc)


@router.patch("/{short_id}/scenes", summary="Edit scene prompts",
              description="Saved for the next regenerate of that scene; nothing is remade here.")
def patch_scenes(short_id: str, body: ScenesPatch, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    for row in body.scenes:
        sc = _scene(ps, row.id)
        if row.image_prompt is not None:
            sc["image_prompt"] = " ".join(row.image_prompt.split())
        if row.motion_prompt is not None:
            sc["motion_prompt"] = " ".join(row.motion_prompt.split())
        svc.put_scene(ps, sc)
    db.commit()
    return _out(db, ps)


@router.post("/{short_id}/continue", response_model=JobOut, status_code=202,
             summary="Approve the step under review and carry on")
def continue_short(short_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    for k in svc.STAGE_KEYS:
        # a failed stage gets another go
        rec = svc.stage(ps, k)
        if rec.get("status") == "failed":
            rec.update(status="pending", detail=None)
            svc.put_stage(ps, k, rec)
    job = svc.queue(db, ps)
    db.commit()
    return job_out(job)


@router.post("/{short_id}/scenes/{scene_id}/regenerate", status_code=202, summary="Make one scene again",
             description="what=keyframe makes a new picture and then a new clip from it; what=clip keeps the "
                         "picture. The other scenes are left as they are.")
def regenerate_scene(short_id: str, scene_id: str, body: RegenerateIn, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    if svc.stage(ps, "scenes").get("status") not in ("review", "done", "failed"):
        raise HTTPException(409, "The scenes haven't been made yet")
    sc = _scene(ps, scene_id)
    if body.what == "clip" and not db.get(Generation, sc.get("keyframe") or ""):
        raise HTTPException(409, "This scene has no picture yet; make the keyframe again instead")
    if body.image_prompt:
        sc["image_prompt"] = " ".join(body.image_prompt.split())
    if body.motion_prompt:
        sc["motion_prompt"] = " ".join(body.motion_prompt.split())
    if body.what == "keyframe":
        sc.update(keyframe=None, keyframe_tries=0)
    sc.update(clip=None, clip_tries=0, clip_from=None, error=None)
    svc.put_scene(ps, sc)
    svc.reset_from(ps, "scenes")
    svc.queue(db, ps)
    db.commit()
    return _out(db, ps)


@router.post("/{short_id}/song/regenerate", response_model=JobOut, status_code=202, summary="Sing the poem again",
             description="A new version of the song; the lines are timed again. Scenes stay, their lengths follow.")
def regenerate_song(short_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    if not ps.lines:
        raise HTTPException(409, "Write the poem first")
    ps.song = {"media_id": (ps.song or {}).get("media_id")}
    flag_modified(ps, "song")
    svc.reset_from(ps, "song")
    job = svc.queue(db, ps)
    db.commit()
    return job_out(job)


@router.post("/{short_id}/render", response_model=JobOut, status_code=202, summary="Cut the Short again",
             description="The video from the current timings and scenes, as a new version of its Library item.")
def render_short(short_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ps = _short(db, cur, short_id)
    svc.ensure_idle(db, ps)
    if not all(svc.done(ps, k) for k in ("poem", "song", "timing", "scenes")):
        raise HTTPException(409, "Finish the poem, song, timing and scenes first")
    svc.reset_from(ps, "render")
    job = svc.queue(db, ps, "render")
    db.commit()
    return job_out(job)
