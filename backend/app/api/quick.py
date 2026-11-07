"""Quick Create (contract v4): one prompt, one autopilot job."""
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.limits import PROMPT_MAX
from app import visibility as vis
from app import autopilot, brand, models_catalog, prompt_enhance
from app import cast as film_cast
from app import mentions as mn
from app.config import get_settings
from app.db import get_db
from app.models import Character, Generation, Job, Project
from app.reel import render_out
from app.schemas import JobOut, ProjectOut, RenderOut
from app.security import CurrentUser, get_current_user, require_editor
from app.services import job_out, project_out

router = APIRouter(prefix="/quick", tags=["quick"])

MIN_S = 5
RECENT = 12
MAX_SCENES = 12
# a scene cut into many shots isn't bound by one long take's limit, so a feature can run this long
FILM_MAX_S = 1200.0


class UpscaleIn(BaseModel):
    engine: Literal["best", "fast", "quick"] = "best"
    target: Literal["1080p", "1440p", "4k"] = "1080p"


class QuickIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=PROMPT_MAX)
    duration_s: float = 30
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    style: Literal["cinematic", "documentary", "animated", "commercial"] = "cinematic"
    dialogue: bool = True
    # contract v25: an Essential character sheet for each cast member, after the portraits (GPU heavy, off)
    character_sheets: bool = False
    upscale: UpscaleIn | None = None
    # contract v6 advanced options; stored on the project so every stage of the autopilot uses them
    image_model: str | None = None
    image_speed: str | None = None
    video_quality: Literal["standard", "hq"] | None = None
    smooth_motion: bool | None = None
    lipsync_engine: Literal["ltx", "wan_s2v"] | None = None
    brand_kit_id: str | None = None  # contract v7: saved as project.settings.brand_kit_id
    template_id: str | None = Field(None, max_length=80)  # its planner notes steer the outline
    brand_closing: Literal["auto", "ai_packshot", "logo_reveal", "none"] | None = None
    magic_prompt: Literal["auto", "on", "off"] | None = None
    prompt_enhanced: bool = False
    # contract v4a: a feature in another language, with a cast from Character Studio
    language: str = Field("en", pattern=r"^[A-Za-z]{2,3}([-_][A-Za-z]{2,4})?$")
    cast_ids: list[str] = Field([], max_length=8)
    scenes: int | None = Field(None, ge=1, le=MAX_SCENES)
    scene_seconds: float | None = Field(None, ge=5, le=600)
    title_intro: bool | None = None
    title: str | None = Field(None, max_length=120)


class QuickOut(BaseModel):
    project: ProjectOut
    job: JobOut


class QuickRecentOut(BaseModel):
    project: ProjectOut
    job: JobOut | None = None
    final_render: RenderOut | None = None


def placeholder_title(prompt: str) -> str:
    words = re.sub(r"\s+", " ", prompt).strip().split(" ")
    title = " ".join(words[:6])
    return (title[:60].rstrip(" ,.;:") + ("…" if len(words) > 6 else "")) or "Quick video"


def film_plan(body: QuickIn) -> tuple[float, int | None, float | None]:
    """(runtime, scenes, seconds a scene). Both scene fields together set the runtime; one alone fills in the other."""
    total, n, per = float(body.duration_s), body.scenes, body.scene_seconds
    if n and per:
        total = n * per
    elif per:
        n = max(1, min(MAX_SCENES, round(total / per)))
        per = total / n
    elif n:
        per = total / n
    return total, n, per


def cast_for(db: Session, cur: CurrentUser, ids: list[str]) -> list[Character]:
    out = []
    for cid in dict.fromkeys(ids):
        c = db.get(Character, cid)
        if c is None or c.workspace_id != cur.workspace_id:
            raise HTTPException(404, f"Character not found: {cid}")
        if c.project_id is not None:
            raise HTTPException(409, f"{c.name} belongs to another film. Save it to Character Studio first.")
        out.append(c)
    return out


@router.post("", response_model=QuickOut, status_code=201)
def quick_create(body: QuickIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    total, n_scenes, per_scene = film_plan(body)
    longest = max(get_settings().longtake_max_s * 4, FILM_MAX_S)
    if not MIN_S <= total <= longest:
        raise HTTPException(422, f"duration_s must be between {MIN_S} and {longest:g} seconds")
    language = body.language.lower().replace("_", "-")
    cast = cast_for(db, cur, body.cast_ids)
    title = " ".join((body.title or "").split())
    intro = body.title_intro if body.title_intro is not None else (language != "en" or total >= 60)
    # TODO: cast mentioned characters into the autopilot's project instead of describing them in words
    mentioned = mn.resolve(db, cur.workspace_id, body.prompt.strip(), budget=0)
    prompt = mentioned.prompt
    choice = {k: getattr(body, k) for k in ("image_model", "image_speed", "video_quality", "smooth_motion",
                                         "lipsync_engine")
              if getattr(body, k) is not None}
    if (body.scenes or body.scene_seconds) and "video_quality" not in choice:
        # a feature is watched at full size; draft-size takes (768x448) look soft once stitched
        choice["video_quality"] = "hq"
    settings = models_catalog.clean_project_settings({}, choice)
    if language != "en":
        settings["language"] = language  # read by every writer prompt for this film, not just the autopilot
    p = Project(workspace_id=cur.workspace_id, title=title or placeholder_title(mn.plain(body.prompt)),
                authoring_mode="quick", brief=prompt,
                aspect_ratio=body.aspect_ratio, target_runtime_s=max(1, round(total)), takes_per_shot=1,
                status="in_progress", style_bible=autopilot.STYLE_PRESETS[body.style][0], settings=settings)
    tpl = None
    if body.template_id:
        from app.api.library import templates

        tpl = templates().get(body.template_id)
        if tpl is None or tpl["type"] != "video":
            raise HTTPException(404, "Template not found")
    if body.brand_kit_id:
        brand.project_kit_id(db, p, body.brand_kit_id)
        if body.brand_closing:
            p.settings = {**(p.settings or {}), "brand_closing": body.brand_closing}
    db.add(p)
    db.flush()
    for c in cast:
        film_cast.link(db, p, c)
    job = Job(workspace_id=cur.workspace_id, type=autopilot.JOB_TYPE, project_id=p.id, message="Waiting for a worker",
              payload={"project_id": p.id, "prompt": prompt, "duration_s": total,
                       "aspect_ratio": body.aspect_ratio, "style": body.style, "dialogue": body.dialogue,
                       "character_sheets": body.character_sheets,
                       "upscale": body.upscale.model_dump() if body.upscale else None, "title_auto": not title,
                       "title": title or None, "language": language, "cast_ids": [c.id for c in cast],
                       "scenes": n_scenes, "scene_seconds": per_scene, "title_intro": intro,
                       "models": settings, "template_id": body.template_id,
                       "planner": (tpl or {}).get("planner") or "", "mentions": mentioned.mentions,
                       "magic_prompt": prompt_enhance.marker(body.magic_prompt, body.prompt_enhanced, "quick",
                                                             style=body.style, brand_kit_id=body.brand_kit_id)},
              result=autopilot.initial_result(body.upscale is not None))
    db.add(job)
    db.commit()
    return QuickOut(project=project_out(db, p), job=job_out(job))


@router.get("/recent", response_model=list[QuickRecentOut])
def recent(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    projects = db.scalars(select(Project).where(Project.workspace_id == cur.workspace_id,
                                                Project.authoring_mode == "quick", *vis.where(Project, vis.wanted(cur)))
                          .order_by(Project.created_at.desc()).limit(RECENT)).all()
    out = []
    for p in projects:
        job = db.scalars(select(Job).where(Job.project_id == p.id, Job.type == autopilot.JOB_TYPE)
                         .order_by(Job.created_at.desc()).limit(1)).first()
        final = None
        fid = (job.result or {}).get("final_render_id") if job else None
        g = db.get(Generation, fid) if fid else None
        if g is not None and g.status in ("ready", "approved"):
            final = render_out(g)
        out.append(QuickRecentOut(project=project_out(db, p), job=job_out(job) if job else None, final_render=final))
    return out
