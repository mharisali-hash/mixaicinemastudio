"""AI video podcast (contract v24): an episode cut between continuous, lip-synced host cameras."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.limits import PROMPT_MAX
from app.ai_jobs import rewrite_prompt_from_note
from app.db import get_db
from app.models import PodcastEpisode, Project, VideoPodcast
from app.podcast import video as vd
from app.podcast import video_stages as vs
from app.podcast import vplan
from app.schemas import GenerationOut, JobOut
from app.security import CurrentUser, get_current_user, require_editor
from app.services import enqueue_generation, gen_out, get_owned, job_out, new_seed

router = APIRouter(prefix="/podcast", tags=["podcast"])

Aspect = Literal["16:9", "9:16", "1:1"]
Quality = Literal["draft", "final"]


class CreateIn(BaseModel):
    aspect: Aspect | None = None


class Consent(BaseModel):
    name: str = Field("", max_length=120)
    statement: str = Field("", max_length=400)


class HostChoice(BaseModel):
    source: Literal["persona", "photo", "existing"]
    media_id: str | None = None
    character_id: str | None = None
    consent: Consent | None = None
    # photo only: use the photo itself as the approved portrait instead of a studio edit of it
    as_is: bool = False


class SettingsIn(BaseModel):
    aspect: Aspect | None = None
    subtitles: Literal["none", "soft", "burn"] | None = None
    set_preset: Literal["modern_studio", "cosy_room", "news_desk", "rooftop", "desi_baithak"] | None = None
    set_prompt: str | None = Field(None, max_length=PROMPT_MAX)
    cover_intro: bool | None = None
    cover_outro: bool | None = None
    cover_media_id: str | None = None


class CutRow(BaseModel):
    id: str | None = None
    camera: str = Field(min_length=1, max_length=64)
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class PatchIn(BaseModel):
    hosts: dict[str, HostChoice] | None = None
    settings: SettingsIn | None = None
    cuts: list[CutRow] | None = Field(None, max_length=5000)


class RegenerateIn(BaseModel):
    what: Literal["portrait", "set", "angle"]
    key: str | None = None
    note: str | None = Field(None, max_length=PROMPT_MAX)


class SwitchIn(BaseModel):
    at: float = Field(ge=0)
    camera: str = Field(min_length=1, max_length=64)


class CutPatch(BaseModel):
    camera: str = Field(min_length=1, max_length=64)


class RenderIn(BaseModel):
    quality: Quality = "draft"
    track_ids: list[str] | None = Field(None, max_length=5000)
    from_piece: int | None = Field(None, ge=1)


class AssembleIn(BaseModel):
    quality: Quality = "draft"


def _vp(db: Session, cur: CurrentUser, vp_id: str) -> VideoPodcast:
    vp = get_owned(db, VideoPodcast, vp_id, cur.workspace_id, "AI video")
    vd.upgrade(db, vp, vd.episode_of(db, vp))
    return vp


def _out(db: Session, vp: VideoPodcast) -> dict[str, Any]:
    return vd.vp_out(db, vp)


@router.get("/episodes/{episode_id}/ai-video")
def get_for_episode(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    ep = get_owned(db, PodcastEpisode, episode_id, cur.workspace_id, "Episode")
    vp = vd.find(db, ep)
    if vp is None:
        raise HTTPException(404, "This episode has no AI video yet")
    return _out(db, vp)


@router.post("/episodes/{episode_id}/ai-video")
def create_for_episode(episode_id: str, body: CreateIn, response: Response, db: Session = Depends(get_db),
                       cur: CurrentUser = Depends(require_editor)):
    ep = get_owned(db, PodcastEpisode, episode_id, cur.workspace_id, "Episode")
    vp, made = vd.create(db, ep, body.aspect)
    db.commit()
    response.status_code = 201 if made else 200
    return _out(db, vp)


@router.get("/ai-video/{vp_id}")
def get_ai_video(vp_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    vp = _vp(db, cur, vp_id)
    vd.sync_hosts(db, vp, vd.episode_of(db, vp))
    db.commit()
    return _out(db, vp)


@router.patch("/ai-video/{vp_id}")
def patch_ai_video(vp_id: str, body: PatchIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    ep = vd.episode_of(db, vp)
    data = body.model_dump(exclude_unset=True)
    resets = []
    if body.hosts or data.get("settings") or body.cuts is not None:
        vd.ensure_idle(db, vp)
    if body.hosts:
        vd.sync_hosts(db, vp, ep)
        for hid, choice in body.hosts.items():
            vd.choose_host(db, cur, vp, ep, hid, choice.model_dump())
        resets.append("cast")
    if data.get("settings"):
        st = vd.settings_of(vp)
        new = {**st, **{k: v for k, v in data["settings"].items() if k in vd.DEFAULT_SETTINGS}}
        if new.get("cover_media_id"):
            from app.podcast import service as sv

            sv.image_item(db, vp.workspace_id, new["cover_media_id"])
        if any(new[k] != st[k] for k in ("aspect", "set_preset", "set_prompt")):
            resets.append("set")
        if new["aspect"] != st["aspect"]:
            db.get(Project, vp.project_id).aspect_ratio = new["aspect"]
        vp.settings = new
        plan = vd.plan_of(vp)
        if plan and (new["cover_intro"], new["cover_outro"]) != (st["cover_intro"], st["cover_outro"]):
            vd.put_cuts(vp, vd.checked(vp, ep, vplan.apply_cover(plan, new["cover_intro"], new["cover_outro"])))
    if body.cuts is not None:
        vd.put_cuts(vp, vd.fill_cuts(db, vp, ep, [r.model_dump() for r in body.cuts]))
    keys = [k for k, _ in vd.STAGES]
    if resets:
        vd.reset_from(vp, min(resets, key=keys.index))
    db.commit()
    return _out(db, vp)


@router.delete("/ai-video/{vp_id}", status_code=204)
def delete_ai_video(vp_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    import shutil

    from app.config import get_settings

    vp = _vp(db, cur, vp_id)
    vd.ensure_idle(db, vp)
    project = db.get(Project, vp.project_id)
    db.delete(vp)
    if project is not None:
        db.delete(project)
    db.commit()
    shutil.rmtree(get_settings().data_dir / "workspaces" / cur.workspace_id / "projects" / vp.project_id,
                  ignore_errors=True)
    return Response(status_code=204)


@router.post("/ai-video/{vp_id}/continue", response_model=JobOut, status_code=202)
def continue_ai_video(vp_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    ep = vd.episode_of(db, vp)
    vd.ensure_idle(db, vp)
    vd.sync_hosts(db, vp, ep)
    if all(vd.stage(vp, k).get("status") == "done" for k in vd.CONTINUE_STAGES):
        key = vd.stale_from(db, vp, ep)
        if key is None:
            raise HTTPException(409, "The hosts, set, look and cameras are up to date: render next")
        vd.reset_from(vp, key)
    if vd.stage(vp, "plan").get("status") != "done" and vd.mix_of(ep) is None and \
            all(vd.stage(vp, k).get("status") == "done" for k in ("cast", "set", "keyframes")):
        raise HTTPException(409, "Mix the episode first: the video follows the mix")
    for k in vd.CONTINUE_STAGES:
        rec = vd.stage(vp, k)
        if rec.get("status") == "failed":
            rec.update(status="pending", detail=None)
            vd.put_stage(vp, k, rec)
    job = vs.queue(db, vp, "continue")
    db.commit()
    return job_out(job)


@router.post("/ai-video/{vp_id}/regenerate", response_model=GenerationOut, status_code=202)
def regenerate(vp_id: str, body: RegenerateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    if body.what == "portrait":
        entry = (vp.hosts or {}).get(body.key or "")
        if not entry:
            raise HTTPException(404, "No such host")
        if entry.get("source") == "existing":
            raise HTTPException(409, "This host is a copied character; pick another source to make a new portrait")
        base = vd.current_portrait(db, entry)
    elif body.what == "set":
        base = vd.establishing(db, vp, fresh_only=False)
    else:
        sid = (vd.refs(vp).get("angle_shots") or {}).get(body.key or "")
        if not sid:
            raise HTTPException(404, "No such angle")
        base = vd.frame_of(db, sid)
    if base is None:
        first = {"portrait": "Make the portraits", "set": "Build the set"}.get(body.what, "Make the camera angles")
        raise HTTPException(409, f"This hasn't been made yet: press “{first}” first, then regenerate it if you want another")
    prompt, note = base.prompt, None
    params = {k: v for k, v in (base.params or {}).items() if k not in ("comfy", "approved_by", "auto_check")}
    if body.what == "angle":
        # from today's angle prompt, not the old frame's: a fix to the template (single-person close-ups,
        # 24 Oct) reaches a regenerate too, and the frame is tagged as current so "continue" keeps it
        prompt, params = vd.fresh_angle_prompt(db, vp, vd.episode_of(db, vp), body.key or "", params)
    if body.note and body.note.strip():
        note = body.note.strip()
        prompt, _ = rewrite_prompt_from_note(prompt, note)
    g = enqueue_generation(db, workspace_id=vp.workspace_id, project_id=vp.project_id, target_type=base.target_type,
                           target_id=base.target_id, kind=base.kind, prompt=prompt, params=params, seed=new_seed(),
                           parent_id=base.id, note=note)
    db.commit()
    return gen_out(g)


@router.get("/ai-video/{vp_id}/characters")
def characters(vp_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return vd.other_characters(db, _vp(db, cur, vp_id))


@router.post("/ai-video/{vp_id}/replan")
def replan(vp_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    ep = vd.episode_of(db, vp)
    vd.ensure_idle(db, vp)
    vd.set_plan(db, vp, vd.build_plan(db, vp, ep))
    rec = vd.stage(vp, "plan")
    rec.update(status="done", basis=vd.mix_of(ep)["generation_id"], detail=None)
    vd.put_stage(vp, "plan", rec)
    db.commit()
    return _out(db, vp)


def _cut_index(vp: VideoPodcast, cut_id: str) -> int:
    i = next((i for i, c in enumerate(vd.cuts_of(vp)) if c["id"] == cut_id), None)
    if i is None:
        raise HTTPException(404, "That cut isn't in the cut list")
    return i


def _save_cuts(db: Session, vp: VideoPodcast, cuts: list[dict]) -> dict:
    vd.put_cuts(vp, vd.checked(vp, vd.episode_of(db, vp), vplan.merge_same(cuts)))
    db.commit()
    return _out(db, vp)


def _editable(db: Session, vp: VideoPodcast) -> list[dict]:
    vd.ensure_idle(db, vp)
    if vd.plan_of(vp) is None:
        raise HTTPException(409, "Plan the cameras first: continue through the hosts, set and look")
    return [dict(c) for c in vd.cuts_of(vp)]


@router.post("/ai-video/{vp_id}/cuts/switch")
def switch_camera(vp_id: str, body: SwitchIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    cuts = _editable(db, vp)
    if not cuts or body.at >= cuts[-1]["end"]:
        raise HTTPException(422, "That time is past the end of the episode")
    return _save_cuts(db, vp, vplan.switch(cuts, body.at, body.camera, (vd.plan_of(vp) or {}).get("snaps") or []))


@router.patch("/ai-video/{vp_id}/cuts/{cut_id}")
def patch_cut(vp_id: str, cut_id: str, body: CutPatch, db: Session = Depends(get_db),
              cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    cuts = _editable(db, vp)
    cuts[_cut_index(vp, cut_id)]["camera"] = body.camera
    return _save_cuts(db, vp, cuts)


@router.delete("/ai-video/{vp_id}/cuts/{cut_id}")
def delete_cut(vp_id: str, cut_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    cuts = _editable(db, vp)
    i = _cut_index(vp, cut_id)
    if len(cuts) < 2:
        raise HTTPException(422, "The episode needs at least one cut")
    # the switch at this cut's start goes: the cut before runs on (the first one hands over to the next)
    if i > 0:
        cuts[i - 1]["end"] = cuts[i]["end"]
    else:
        cuts[1]["start"] = 0.0
    del cuts[i]
    return _save_cuts(db, vp, cuts)


@router.get("/ai-video/{vp_id}/estimate")
def estimate(vp_id: str, quality: Quality = "draft", track_ids: str | None = Query(None),
             db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    vp = _vp(db, cur, vp_id)
    ids = [s for s in (track_ids or "").split(",") if s] or None
    return vd.estimate(db, vp, vd.episode_of(db, vp), quality, ids)


def _ready_to_render(db: Session, vp: VideoPodcast) -> None:
    if vd.stage(vp, "plan").get("status") != "done" or not vd.tracks_of(vp):
        raise HTTPException(409, "Plan the cameras first: continue through the hosts, set and look")
    frames = vd.angle_frames(db, vp)
    missing = {t["angle_key"] for t in vd.used_tracks(vp) if t["angle_key"] not in frames}
    if missing:
        raise HTTPException(409, f"Some camera angles have no frame yet ({', '.join(sorted(missing)[:4])})")


def _check_from_piece(db: Session, vp: VideoPodcast, body: RenderIn) -> None:
    if not body.track_ids or len(body.track_ids) != 1:
        raise HTTPException(422, "Re-rendering from a piece works on one camera track at a time")
    track = next(t for t in vd.tracks_of(vp) if t["id"] == body.track_ids[0])
    if body.from_piece > int(track.get("pieces") or 1):
        raise HTTPException(422, f"This track has {track.get('pieces') or 1} pieces")
    newest = next(iter(vd.takes_for(db, [track["id"]])[track["id"]][body.quality]), None)
    sig = vd.Inputs(db, vp, vd.episode_of(db, vp)).sig(track, body.quality)
    if newest is None or (newest.params or {}).get("sig") != sig or not (newest.params or {}).get("chunks"):
        raise HTTPException(409, "This track's inputs changed since its last take; render it from the start")


@router.post("/ai-video/{vp_id}/render", response_model=JobOut, status_code=202)
def render(vp_id: str, body: RenderIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    vd.ensure_idle(db, vp)
    _ready_to_render(db, vp)
    known = {t["id"] for t in vd.tracks_of(vp)}
    if body.track_ids and not set(body.track_ids) <= known:
        raise HTTPException(404, "Some of those camera tracks aren't in the plan")
    if body.from_piece and body.from_piece > 1:
        _check_from_piece(db, vp, body)
    for k in ("render", "assemble"):
        vd.put_stage(vp, k, {"status": "pending"})
    job = vs.queue(db, vp, "render", quality=body.quality, track_ids=body.track_ids, from_piece=body.from_piece)
    db.commit()
    return job_out(job)


@router.post("/ai-video/{vp_id}/assemble", response_model=JobOut, status_code=202)
def assemble(vp_id: str, body: AssembleIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    vp = _vp(db, cur, vp_id)
    vd.ensure_idle(db, vp)
    _ready_to_render(db, vp)
    vd.put_stage(vp, "assemble", {"status": "pending"})
    job = vs.queue(db, vp, "assemble", quality=body.quality)
    db.commit()
    return job_out(job)

