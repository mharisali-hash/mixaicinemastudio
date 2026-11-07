from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import brand_moments as bm
from app import cast, character_panels as cp
from app import camera as cam
from app import film_audio, longtake, shot_speech
from app import storyboard as sb
from app.ai_jobs import active_job, enqueue_ai
from app.config import get_settings
from app.db import get_db
from app.models import Character, Generation, Job, Location, Project, Scene, Shot
from app.schemas import (ChunkRegenerateIn, JobOut, SceneOut, ShotCreate, ShotExtendIn, ShotExtendOut, ShotMergeIn,
                         ShotOut, ShotPatch, ShotReorderIn, ShotRerenderIn, ShotRerenderOut, ShotsApproveIn,
                         ShotsApproveOut, ShotsReviewIn, ShotSplitIn, TakeEstimate, TakesIn)
from app.security import CurrentUser, get_current_user, require_editor
from app.services import enqueue_generation, gen_out, get_owned, job_out, new_seed

router = APIRouter(tags=["shots"])

LOCK_FIELDS = ("description", "prompt", "camera")
PROMPT_FIELDS = ("start_prompt", "end_prompt", "motion_prompt")


def _check_refs(db: Session, project_id: str, character_ids: list[str] | None, location_id: str | None) -> None:
    if character_ids:
        found = set(db.scalars(
            select(Character.id).where(cast.in_project(project_id), Character.id.in_(character_ids))
        ).all())
        if found != set(character_ids):
            raise HTTPException(422, "character_ids must be characters of this project")
    if location_id:
        loc = db.get(Location, location_id)
        if loc is None or loc.project_id != project_id:
            raise HTTPException(422, "location_id is not a location of this project")


@router.get("/scenes/{scene_id}/shots", response_model=list[ShotOut])
def list_scene_shots(scene_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    return sb.shots_out(db, sb.scene_shots(db, scene_id))


@router.get("/projects/{project_id}/shots", response_model=list[ShotOut])
def list_project_shots(project_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    get_owned(db, Project, project_id, cur.workspace_id, "Project")
    return sb.shots_out(db, sb.project_shots(db, project_id))


@router.get("/shots/{shot_id}", response_model=ShotOut)
def get_shot(shot_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return sb.shot_out(db, get_owned(db, Shot, shot_id, cur.workspace_id, "Shot"))


@router.get("/shots/{shot_id}/refs")
def shot_refs(shot_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    """The pictures a keyframe of this shot is drawn with (contract v25): per character the sheet panel the
    framing picked (or the user's pick), then the location. The first MAX_REFS usable ones are used."""
    from app import character_sheet as cs

    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    out = []
    for cid in shot.character_ids or []:
        c = db.get(Character, cid)
        if c is None:
            continue
        r = sb.character_ref(db, shot, c)
        options = [{"key": "portrait", "label": "Portrait", "section_title": "Portrait"}]
        for key, gens in cs.panel_gens(db, c.id).items():
            p = cp.panel(key, cs.extras(c))
            if p is not None and p.source != "none" and cs.current(gens) is not None:
                options.append({"key": key, "label": cs.panel_label(c, p), "section_title": cs.section_title(c, p.section)})
        from app import character_looks as cl

        looks = {cl.ref_key(x.id): x.name for x in cl.looks_of(db, c) if cl.finished_picture(db, c, x.id)}
        options += [{"key": k, "label": name, "section_title": "Looks"} for k, name in looks.items()]
        p = cp.panel(r["panel"] or "", cs.extras(c))
        label = cs.panel_label(c, p) if p else looks.get(r["panel"] or "") or ("Portrait" if r["panel"] else None)
        out.append({"kind": "character", "id": c.id, "name": c.name, "panel": r["panel"],
                    "panel_label": label,
                    "auto": r["auto"], "generation": gen_out(r["generation"]) if r["generation"] else None,
                    "options": options})
    loc_id = shot.location_id or (db.get(Scene, shot.scene_id).location_id if shot.scene_id else None)
    loc = db.get(Location, loc_id) if loc_id else None
    if loc is not None:
        g = sb.approved(db, "location", loc.id, "establishing")
        out.append({"kind": "location", "id": loc.id, "name": loc.name, "panel": None, "panel_label": None,
                    "auto": True, "generation": gen_out(g) if g is not None and g.file_path else None, "options": []})
    n = 0
    for r in out:
        r["used"] = r["generation"] is not None and n < sb.MAX_REFS
        n += r["used"]
    return {"max_refs": sb.MAX_REFS, "refs": out}


@router.post("/scenes/{scene_id}/shots", response_model=ShotOut, status_code=201)
def create_shot(scene_id: str, body: ShotCreate, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    scene = get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    _check_refs(db, scene.project_id, body.character_ids, body.location_id)
    shots = sb.scene_shots(db, scene.id)
    insert_at = len(shots)
    if body.after_shot_id:
        idx = next((i for i, s in enumerate(shots) if s.id == body.after_shot_id), None)
        if idx is None:
            raise HTTPException(422, "after_shot_id is not a shot of this scene")
        insert_at = idx + 1
    shot = Shot(
        workspace_id=cur.workspace_id,
        project_id=scene.project_id,
        scene_id=scene.id,
        shot_type=body.shot_type or ("long_take" if longtake.is_long(body.duration_s) else "medium"),
        duration_s=body.duration_s,
        description=body.description,
        camera=body.camera,
        character_ids=body.character_ids or [],
        location_id=body.location_id or scene.location_id,
        seam_in=body.seam_in,
        source="user",
        locked=bool(body.description.strip()),
    )
    shots.insert(insert_at, shot)
    sb.renumber(shots)
    db.add(shot)
    db.commit()
    return sb.shot_out(db, shot)


@router.patch("/shots/{shot_id}", response_model=ShotOut)
def patch_shot(shot_id: str, body: ShotPatch, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    changes = body.model_dump(exclude_unset=True)
    _check_refs(db, shot.project_id, changes.get("character_ids"), changes.get("location_id"))

    for flag in ("locked", "stale"):
        if changes.get(flag) is not None:
            setattr(shot, flag, changes.pop(flag))
        changes.pop(flag, None)
    if changes.get("brand_placements") is not None:
        # model_dump drops nothing extra; a placement without a source is the user's (locked)
        changes["brand_placements"] = [{**p, "source": p.get("source") or "user"} for p in changes["brand_placements"]]

    if changes.get("camera_rack") is not None:
        rack = {k: v for k, v in changes["camera_rack"].items() if v}
        changes["camera_rack"] = rack
        if "camera" not in changes:
            changes["camera"] = cam.to_prompt(rack).rstrip(".")[:300]
    if "ref_panels" in changes:
        picks = changes.pop("ref_panels") or {}
        merged = {**(shot.ref_panels or {}), **picks}
        # a blank or "auto" pick goes back to the automatic choice
        shot.ref_panels = {k: v for k, v in merged.items()
                           if v and v != "auto" and (cp.panel(v) is not None or v.startswith("look:"))}
    if "dialogue" in changes:
        lines = changes.pop("dialogue")
        if lines is not None:
            speakers = changes.get("character_ids") if changes.get("character_ids") is not None else shot.character_ids
            new = shot_speech.clean_lines(lines, speakers)
            if new != (shot.dialogue or []) and sb.has_gens(db, shot.id, ("take",)):
                shot.stale = True
            shot.dialogue = new
    if "beats" in changes:
        beats = changes.pop("beats")
        if beats is not None:
            shot.beats = _merge_beats(shot.beats or [], beats)
    if changes.get("duration_s") is not None and changes["duration_s"] != shot.duration_s:
        # beats are timed to the old chunk plan; unlocked ones get rewritten on the next render
        longtake.mark_beats_stale(shot)
        if "shot_type" not in changes and longtake.is_long(changes["duration_s"]):
            changes["shot_type"] = "long_take"

    edited: set[str] = set()
    for field, value in changes.items():
        if value is None and field != "location_id":
            continue
        if value != getattr(shot, field):
            edited.add(field)
        setattr(shot, field, value)
    if "character_ids" in edited:
        shot_speech.keep_speakers(shot)

    # same lock rule as scenes: the user's words are never silently replaced by AI
    if edited & set(LOCK_FIELDS):
        shot.source = "ai_edited" if shot.source in ("ai", "ai_edited") else "user"
        shot.locked = True
    if edited & set(PROMPT_FIELDS) and "prompt_mode" not in changes:
        shot.prompt_mode = "manual"
    if "seam_in" in edited and sb.has_gens(db, shot.id, sb.SHOT_KINDS):
        shot.stale = True
    db.commit()
    return sb.shot_out(db, shot)


@router.post("/scenes/{scene_id}/shots/reorder", response_model=list[ShotOut])
def reorder_shots(scene_id: str, body: ShotReorderIn, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    shots = {s.id: s for s in sb.scene_shots(db, scene_id)}
    if len(body.shot_ids) != len(set(body.shot_ids)) or set(body.shot_ids) != set(shots):
        raise HTTPException(422, "shot_ids must list every shot of the scene exactly once")
    ordered = [shots[i] for i in body.shot_ids]
    before = sb.link_map(db, ordered[0].project_id) if ordered else {}
    sb.renumber(ordered)
    if ordered:
        # a Continue seam that now follows another shot opens on a different END frame
        sb.restale_links(db, ordered[0].project_id, before)
    db.commit()
    return sb.shots_out(db, ordered)


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    scene_id = shot.scene_id
    _, nxt = sb.neighbours(db, shot)
    if nxt is not None and nxt.seam_in == "continue" and sb.has_gens(db, nxt.id, sb.SHOT_KINDS):
        nxt.stale = True
    sb.delete_shots(db, [shot])
    db.flush()
    sb.renumber(sb.scene_shots(db, scene_id))
    db.commit()
    return Response(status_code=204)


@router.post("/shots/{shot_id}/clear-stale", response_model=ShotOut)
def clear_stale(shot_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    shot.stale = False
    db.commit()
    return sb.shot_out(db, shot)


def _merge_beats(old: list[dict], new: list[dict]) -> list[dict]:
    """Every beat the user changed gets locked (source user/ai_edited); untouched ones keep their state."""
    out = []
    for i, b in enumerate(new):
        prev = old[i] if i < len(old) else None
        b = dict(b)
        if b["t_end"] < b["t_start"]:
            raise HTTPException(422, f"beat {i + 1}: t_end is before t_start")
        if prev is None or (prev.get("prompt") or "").strip() != b["prompt"].strip():
            b["source"] = "ai_edited" if prev is not None and prev.get("source") in ("ai", "ai_edited") else "user"
            b["locked"] = True
        elif prev.get("stale") and (prev.get("t_start"), prev.get("t_end")) == (b["t_start"], b["t_end"]):
            b["stale"] = True
        out.append(b)
    return out


def _take_count(body: TakesIn | None, project: Project, long: bool) -> int:
    if body and body.count:
        return body.count
    # long takes are expensive: one unless asked
    return 1 if long else project.takes_per_shot


def _queue_takes(db: Session, shot: Shot, count: int, cur: CurrentUser, duration_s: float | None = None,
                 render: dict | None = None) -> list[Job]:
    params = {"duration_s": duration_s} if duration_s else {}
    params.update({k: v for k, v in (render or {}).items() if v is not None})
    prompt, params = sb.prepare_shot_generation(db, shot, "take", "", params)
    # the shot's lines are spoken first and shared by every take (contract v26)
    shot_speech.queue_lines(db, cur, params)
    params["created_by"] = {"user_id": cur.id, "flow": "shot_take"}
    jobs = []
    for _ in range(count):
        # one seed each, queued back to back so LTX stays loaded between them
        g = enqueue_generation(db, workspace_id=shot.workspace_id, project_id=shot.project_id, target_type="shot",
                               target_id=shot.id, kind="take", prompt=prompt, params=dict(params))
        jobs.append(db.get(Job, g.job_id))
    return jobs


@router.post("/shots/{shot_id}/takes", response_model=list[JobOut], status_code=202)
def queue_takes(shot_id: str, body: TakesIn | None = None, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    if bm.is_reveal(db, shot):
        # the closing logo reveal is rendered from the kit's logo file, never by LTX
        kit = bm.project_kit(db, db.get(Project, shot.project_id))
        if kit is None:
            raise HTTPException(422, "The project's brand kit is gone; pick one to render the logo reveal")
        g = bm.queue_reveal_take(db, shot, kit)
        db.commit()
        return [job_out(db.get(Job, g.job_id))] if g.job_id else []
    duration = (body.duration_s if body else None) or shot.duration_s
    count = _take_count(body, db.get(Project, shot.project_id), longtake.is_long(duration))
    jobs = _queue_takes(db, shot, count, cur, duration, _render_choice(body))
    db.commit()
    return [job_out(j) for j in jobs]


def _render_choice(body: TakesIn | None) -> dict:
    return {"quality": body.quality, "smooth_motion": body.smooth_motion} if body else {}


@router.post("/scenes/{scene_id}/render", response_model=list[JobOut], status_code=202)
def render_scene(scene_id: str, body: TakesIn | None = None, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    scene = get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    project = db.get(Project, scene.project_id)
    jobs = []
    for shot in sb.scene_shots(db, scene.id):
        start, _ = sb.take_frames(db, shot)
        if start is not None:
            long = longtake.is_long(shot.duration_s)
            # the scene-wide count is meant for normal shots; a long take stays at one
            # (re-roll it from the shot if needed) so one click can't queue hours of GPU
            count = 1 if long else _take_count(body, project, long)
            render = _render_choice(body)
            if long:
                # a scene-wide HQ request shouldn't 422 because one shot is a long take
                render = {}
            jobs += _queue_takes(db, shot, count, cur, render=render)
    if not jobs:
        raise HTTPException(409, "No shot in this scene has an approved START frame yet")
    db.commit()
    return [job_out(j) for j in jobs]


# ---------------------------------------------------------------- voice first (contract v4a addendum)
# A scene's dialogue is spoken first and its shots cut from the measured track; each step runs per scene, so
# a scene added or edited by hand builds the same way as the autopilot's.

def _scene_for_audio(db: Session, scene_id: str, cur: CurrentUser) -> Scene:
    scene = get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    if not scene.script_text.strip():
        raise HTTPException(409, "This scene has no script yet")
    return scene


def _voiced(out: dict) -> dict:
    return {"status": out["status"], "lines": out["lines"], "job_ids": out["job_ids"]}


@router.post("/scenes/{scene_id}/voice", status_code=202)
def voice_scene(scene_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Speak (or re-speak) the scene's lines in their characters' voices; unchanged lines keep their takes."""
    scene = _scene_for_audio(db, scene_id, cur)
    out = film_audio.voice_scene(db, scene, cur.id)
    db.commit()
    return _voiced(out)


@router.post("/scenes/{scene_id}/plan-shots")
def plan_scene_shots(scene_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Cut the scene into shots from its spoken lines. Shots whose lines didn't change are kept."""
    scene = _scene_for_audio(db, scene_id, cur)
    out = film_audio.plan_scene(db, scene)
    db.commit()
    return out


@router.post("/scenes/{scene_id}/build", status_code=202)
def build_scene(scene_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Voice, then (once every line is spoken) cut the shots and queue the new shots' frames. Call again
    while it says "voicing"."""
    from app.ai_jobs import queue_frames

    scene = _scene_for_audio(db, scene_id, cur)
    out = _voiced(film_audio.voice_scene(db, scene, cur.id))
    if out["status"] == "voiced":
        planned = film_audio.plan_scene(db, scene)
        frames = [g.job_id for sid in planned["new_shot_ids"] for g in queue_frames(db, db.get(Shot, sid))]
        out.update(status="planned", **planned, frame_job_ids=frames)
    db.commit()
    return out


@router.get("/scenes/{scene_id}/audio")
def scene_audio(scene_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    """The scene's dialogue track and whether its shots still match its lines."""
    scene = get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    return film_audio.status(db, scene)


# ---------------------------------------------------------------- long takes (contract v2)

@router.get("/shots/{shot_id}/estimate", response_model=TakeEstimate)
def estimate_take(shot_id: str, duration_s: float | None = Query(None), db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(get_current_user)):
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    duration = duration_s if duration_s is not None else shot.duration_s
    if not 1 <= duration <= get_settings().longtake_max_s:
        raise HTTPException(422, f"duration_s must be between 1 and {get_settings().longtake_max_s:g} seconds")
    return longtake.estimate(duration, longtake.measured_rate(db, cur.workspace_id))


@router.post("/shots/{shot_id}/ai/beats", response_model=JobOut, status_code=202)
def write_beats(shot_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    running = active_job(db, "ai_beats", shot.project_id, shot_id=shot.id)
    if running:
        return job_out(running)
    job = enqueue_ai(db, workspace_id=cur.workspace_id, type="ai_beats", project_id=shot.project_id,
                     payload={"shot_id": shot.id, "duration_s": shot.duration_s,
                              "old_duration_s": longtake.beats_span(shot.beats)})
    job.priority = 5
    db.commit()
    return job_out(job)


@router.post("/generations/{gen_id}/chunks/{idx}/regenerate", response_model=JobOut, status_code=202)
def regenerate_chunk(gen_id: str, idx: int, body: ChunkRegenerateIn | None = None, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    """New take version: chunks before idx are reused, idx..end are rendered again, then rejoined."""
    parent = get_owned(db, Generation, gen_id, cur.workspace_id, "Generation")
    if parent.kind != "take" or not (parent.params or {}).get("longtake"):
        raise HTTPException(409, "Only long takes are made of chunks")
    if parent.status in ("queued", "generating"):
        raise HTTPException(409, "This take is still rendering")
    body = body or ChunkRegenerateIn()
    try:
        params = longtake.reroll_params(parent, idx, body.prompt, body.seed)
    except longtake.LongTakeError as e:
        raise HTTPException(422, str(e)) from None
    g = enqueue_generation(db, workspace_id=parent.workspace_id, project_id=parent.project_id,
                           target_type=parent.target_type, target_id=parent.target_id, kind="take",
                           prompt=parent.prompt, params=params, seed=new_seed(), parent_id=parent.id)
    longtake.copy_kept_chunks(parent, g)
    job = db.get(Job, g.job_id)
    redo = len(params["chunks"]) - idx
    job.message = f"Waiting for a worker · redoing {redo} of {len(params['chunks'])} chunks"
    db.commit()
    return job_out(job)


# ---------------------------------------------------------------- shot list editing (P3)

@router.post("/shots/merge", response_model=list[ShotOut])
def merge_shots(body: ShotMergeIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    if len(set(body.shot_ids)) != len(body.shot_ids):
        raise HTTPException(422, "shot_ids lists a shot twice")
    shots = [get_owned(db, Shot, i, cur.workspace_id, "Shot") for i in body.shot_ids]
    kept = sb.merge_shots(db, shots)
    db.commit()
    return sb.shots_out(db, sb.scene_shots(db, kept.scene_id))


@router.post("/shots/{shot_id}/split", response_model=list[ShotOut])
def split_shot(shot_id: str, body: ShotSplitIn | None = None, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    body = body or ShotSplitIn()
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    sb.split_shot(db, shot, body.at_ratio, body.descriptions)
    db.commit()
    return sb.shots_out(db, sb.scene_shots(db, shot.scene_id))


EXTEND_DEFAULT_S = 5.0


@router.post("/shots/{shot_id}/extend", response_model=ShotExtendOut, status_code=201)
def extend_shot(shot_id: str, body: ShotExtendIn | None = None, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    """The next beat as a new shot that opens on this one's END frame; the AI writes what happens."""
    body = body or ShotExtendIn()
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    if bm.closing_of(db, shot):
        raise HTTPException(409, "The advert's closing shot ends the film; extend the shot before it instead")
    duration = body.duration_s or EXTEND_DEFAULT_S
    hint = (body.prompt or "").strip()
    new = Shot(
        workspace_id=shot.workspace_id, project_id=shot.project_id, scene_id=shot.scene_id,
        shot_type="long_take" if longtake.is_long(duration) else
        (shot.shot_type if shot.shot_type not in ("long_take", "establishing") else "medium"),
        duration_s=duration, description=hint, camera=shot.camera,
        character_ids=list(shot.character_ids or []), location_id=shot.location_id, seam_in="continue",
        brand_placements=[{**p, "source": "ai"} for p in shot.brand_placements or []],
        source="user" if hint else "ai", locked=bool(hint), prompt_mode="auto",
    )
    sb.insert_after(db, shot, new)
    job = enqueue_ai(db, workspace_id=cur.workspace_id, type="ai_extend_shot", project_id=shot.project_id,
                     payload={"shot_id": new.id, "from_shot_id": shot.id, "hint": hint})
    db.commit()
    return ShotExtendOut(shot=sb.shot_out(db, new), job=job_out(job))


@router.post("/shots/{shot_id}/rerender", response_model=ShotRerenderOut, status_code=202)
def rerender_shot(shot_id: str, body: ShotRerenderIn | None = None, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    """New versions of this shot's frames or takes only. Neighbours are never queued; a Continue shot after
    this one is reported so the UI can warn (it goes stale when a new END is approved)."""
    body = body or ShotRerenderIn()
    shot = get_owned(db, Shot, shot_id, cur.workspace_id, "Shot")
    if bm.is_reveal(db, shot):
        raise HTTPException(409, "The logo reveal is rendered from the brand kit's logo; use Render takes")
    _, nxt = sb.neighbours(db, shot)
    if body.what == "takes":
        project = db.get(Project, shot.project_id)
        jobs = _queue_takes(db, shot, _take_count(TakesIn(count=body.count), project, longtake.is_long(shot.duration_s)),
                            cur)
        linked = None
    else:
        prev, _ = sb.neighbours(db, shot)
        kinds = ["keyframe_end"] if sb.is_linked(shot, prev) else ["keyframe_start", "keyframe_end"]
        jobs = []
        for kind in kinds:
            prompt, params = sb.prepare_shot_generation(db, shot, kind, "", dict(body.params or {}))
            g = enqueue_generation(db, workspace_id=shot.workspace_id, project_id=shot.project_id, target_type="shot",
                                   target_id=shot.id, kind=kind, prompt=prompt, params=params)
            jobs.append(db.get(Job, g.job_id))
        linked = nxt.id if nxt is not None and nxt.seam_in == "continue" else None
    db.commit()
    return ShotRerenderOut(jobs=[job_out(j) for j in jobs], linked_next_shot_id=linked)


@router.patch("/scenes/{scene_id}/shots-review", response_model=SceneOut)
def set_shots_review(scene_id: str, body: ShotsReviewIn, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    scene = get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    sb.set_review(db.get(Project, scene.project_id), scene.id, "pending" if body.status == "pending" else None)
    db.commit()
    return scene


@router.post("/scenes/{scene_id}/shots-review/approve", response_model=ShotsApproveOut, status_code=202)
def approve_shot_list(scene_id: str, body: ShotsApproveIn | None = None, db: Session = Depends(get_db),
                      cur: CurrentUser = Depends(require_editor)):
    """Ends the review gate and queues the keyframes it held back (only the ones not made yet)."""
    body = body or ShotsApproveIn()
    scene = get_owned(db, Scene, scene_id, cur.workspace_id, "Scene")
    shots = sb.scene_shots(db, scene.id)
    if not shots:
        raise HTTPException(409, "This scene has no shots to approve yet")
    sb.set_review(db.get(Project, scene.project_id), scene.id, None)
    jobs = []
    if body.generate_frames:
        for shot in shots:
            if bm.is_reveal(db, shot):
                continue
            prev, _ = sb.neighbours(db, shot)
            for kind in (["keyframe_end"] if sb.is_linked(shot, prev) else ["keyframe_start", "keyframe_end"]):
                if sb.has_gens(db, shot.id, (kind,)):
                    continue
                prompt, params = sb.prepare_shot_generation(db, shot, kind, "", dict(body.params or {}))
                g = enqueue_generation(db, workspace_id=shot.workspace_id, project_id=shot.project_id,
                                       target_type="shot", target_id=shot.id, kind=kind, prompt=prompt, params=params)
                jobs.append(db.get(Job, g.job_id))
    db.commit()
    return ShotsApproveOut(scene=SceneOut.model_validate(scene), jobs=[job_out(j) for j in jobs])
