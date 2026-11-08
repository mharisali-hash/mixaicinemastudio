"""Dub a video (contract v18): projects, the review checkpoints, edits, regeneration and export."""
import shutil

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import visibility as vis
from app import audio_jobs as aj
from app import library as lib
from app import transcripts as tr
from app.db import get_db
from app.dub import project as pj
from app.dub.schemas import DubExportIn, DubIn, DubOut, DubPatch, MergeIn, RegenIn, SplitIn
from app.models import DubProject, Generation, Job, new_id
from app.security import CurrentUser, get_current_user, require_editor, require_feature
from app.services import get_owned, job_out, new_seed, next_version

router = APIRouter(prefix="/dubs", tags=["dub"], dependencies=[Depends(require_feature("dub"))])


def _owned(db: Session, cur: CurrentUser, dub_id: str) -> DubProject:
    return get_owned(db, DubProject, dub_id, cur.workspace_id, "Dub")


@router.post("", status_code=202, response_model=DubOut)
def create_dub(body: DubIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    item, g = tr.media_source(db, cur.workspace_id, body.video_id, video_only=True)
    settings = {**pj.DEFAULT_SETTINGS, **(body.settings.model_dump(exclude_none=True) if body.settings else {}),
                "speakers": body.speakers}
    src = (body.source_language or "").lower() or None
    dub = DubProject(id=new_id(), workspace_id=cur.workspace_id, video_id=item.id, video_generation_id=g.id,
                     title=(body.title or "").strip() or item.title or "Dub",
                     source_language=None if src == "auto" else src,
                     targets=list(dict.fromkeys(t.lower() for t in body.targets)), stage="extract",
                     stages=pj.new_stages(), speakers=[], segments=[], glossary=[], settings=settings, tracks={})
    db.add(dub)
    db.flush()
    pj.queue_stage(db, dub, "extract")
    db.commit()
    return pj.dub_out(db, dub)


@router.get("")
def list_dubs(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(DubProject).where(DubProject.workspace_id == cur.workspace_id,
                                               *vis.where(DubProject, vis.wanted(cur)))
                      .order_by(DubProject.updated_at.desc(), DubProject.id).limit(200)).all()
    return [pj.dub_out(db, d, light=True) for d in rows]


@router.get("/{dub_id}")
def get_dub(dub_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return pj.dub_out(db, _owned(db, cur, dub_id))


@router.patch("/{dub_id}")
def patch_dub(dub_id: str, body: DubPatch, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    data = body.model_dump(exclude_unset=True)
    if any(k in data for k in ("segments", "speakers", "targets")):
        # the running stage writes these back whole; an edit now would be lost or half applied
        pj.ensure_idle(db, dub)
    if data.get("title"):
        dub.title = data["title"].strip()
    if "targets" in data and data["targets"]:
        pj.set_targets(dub, data["targets"])
    if data.get("speakers"):
        pj.edit_speakers(db, dub, data["speakers"])
    if data.get("segments"):
        pj.edit_segments(dub, data["segments"], pj.speaker_map(dub))
    if "glossary" in data:
        dub.glossary = [{"source": " ".join(g["source"].split()),
                         "target": {k.lower(): " ".join(v.split()) for k, v in (g.get("target") or {}).items() if v},
                         **({"note": g["note"]} if g.get("note") else {})} for g in data["glossary"] or []]
    if data.get("settings"):
        dub.settings = {**pj.DEFAULT_SETTINGS, **(dub.settings or {}),
                        **{k: v for k, v in data["settings"].items() if v is not None}}
    db.commit()
    return pj.dub_out(db, dub)


@router.delete("/{dub_id}", status_code=204)
def delete_dub(dub_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    job = pj.active_job(db, dub)
    if job is not None:
        job.status, job.message = "cancelled", "Cancelled: dub deleted"
    root, _ = pj.dub_dir(dub)
    db.delete(dub)
    db.commit()
    # the tracks and voice samples are Library items and stay there
    shutil.rmtree(root, ignore_errors=True)
    return Response(status_code=204)


@router.post("/{dub_id}/continue", status_code=202)
def continue_dub(dub_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    pj.ensure_idle(db, dub)
    stage = pj.first_open(dub)
    if stage is None:
        raise HTTPException(409, "Every step is done; change some lines and regenerate them instead")
    if dub.stages[stage]["status"] == "review":
        pj.set_stage(dub, stage, status="done")
        i = pj.STAGES.index(stage)
        if i + 1 == len(pj.STAGES):
            db.commit()
            return pj.dub_out(db, dub)
        stage = pj.STAGES[i + 1]
    pj.queue_stage(db, dub, stage)
    db.commit()
    return pj.dub_out(db, dub)


@router.post("/{dub_id}/regen", status_code=202)
def regen(dub_id: str, body: RegenIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    pj.ensure_idle(db, dub)
    lang = body.lang.lower()
    if lang not in (dub.targets or []):
        raise HTTPException(422, f"{lang} isn't one of this dub's languages")
    translated = (dub.stages or {}).get("translate", {}).get("status")
    if translated == "review":
        raise HTTPException(409, "Check the translation and continue first; regenerating comes after the first mix")
    if translated != "done":
        raise HTTPException(409, "Translate the dub first (continue the pipeline)")
    segs = {s["id"]: s for s in dub.segments or []}
    if body.segment_ids:
        unknown = [i for i in body.segment_ids if i not in segs]
        if unknown:
            raise HTTPException(404, f"Segment {unknown[0]} not found")
        ids = list(dict.fromkeys(body.segment_ids))
    else:
        ids = [i for i, s in segs.items() if pj.target(s, lang).get("status", "pending") in
               ("stale", "pending", "failed", "translated")]
    if not ids:
        raise HTTPException(422, "Nothing has changed in this language")
    start = "translate" if any(pj.target(segs[i], lang).get("status", "pending") == "pending" for i in ids) \
        else "render"
    pj.reset_from(dub, start)
    if start == "render":
        # cast is a no-op once speakers have voices, but a never-cast dub needs it
        if (dub.stages or {}).get("cast", {}).get("status") != "done":
            start = "cast"
            pj.reset_from(dub, "cast")
    pj.queue_stage(db, dub, start, {"langs": [lang], "only": {lang: ids}, "regen": True})
    db.commit()
    return {**pj.dub_out(db, dub), "regen": {"lang": lang, "segment_ids": ids, "from": start}}


@router.post("/{dub_id}/segments/split")
def split(dub_id: str, body: SplitIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    pj.ensure_idle(db, dub)
    pj.split_segment(dub, body.segment_id, body.at)
    db.commit()
    return pj.dub_out(db, dub)


@router.post("/{dub_id}/segments/merge")
def merge(dub_id: str, body: MergeIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    pj.ensure_idle(db, dub)
    pj.merge_segments(dub, body.segment_ids)
    db.commit()
    return pj.dub_out(db, dub)


@router.post("/{dub_id}/export", status_code=202)
def export(dub_id: str, body: DubExportIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    dub = _owned(db, cur, dub_id)
    langs = list(dict.fromkeys(x.lower() for x in body.langs))
    missing = [x for x in langs if x not in (dub.tracks or {})]
    if missing:
        raise HTTPException(422, f"There's no finished {missing[0]} dub yet; run the pipeline to the mix first")
    default = body.default_lang.lower()
    if default not in langs:
        raise HTTPException(422, "The default language has to be one of the exported ones")
    item, _ = tr.media_source(db, cur.workspace_id, dub.video_id, video_only=True)
    src =db.get(Generation, dub.video_generation_id)
    if src is None:
        raise HTTPException(409, "The dubbed version of this video is gone")
    keep = {k: v for k, v in (src.params or {}).items() if k in aj.VIDEO_KEEP}
    payload = {"dub_id": dub.id, "langs": langs, "default_lang": default, "subtitles": body.subtitles,
               "include_original": body.include_original, "stems": body.stems}
    g = Generation(id=new_id(), workspace_id=cur.workspace_id, project_id=None, target_type="media",
                   target_id=item.id, kind="video", version=next_version(db, "media", item.id, "video"),
                   status="queued", prompt=src.prompt or "", seed=new_seed(), parent_id=src.id,
                   note=body.note or f"Dubbed: {', '.join(langs)}",
                   params={**keep, "has_audio": True, "dub": payload, "source_id": src.id,
                           "created_by": {"user_id": cur.id, "flow": "dub"}})
    db.add(g)
    db.flush()
    job = Job(workspace_id=cur.workspace_id, type=pj.EXPORT_JOB, generation_id=g.id,
              message="Waiting for a worker · Dub export", payload={"generation_id": g.id, **payload})
    db.add(job)
    db.flush()
    g.job_id = job.id
    lib.touch(item)
    db.commit()
    return {"job": job_out(job), "item_id": item.id, "generation_id": g.id}
