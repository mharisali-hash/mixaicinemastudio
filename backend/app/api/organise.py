"""Folders, favourites, saved filters and batch actions for the Library (contract v8, P4)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import visibility as vis
from app import library as lib
from app import organise as org
from app import upscale, upscale_image
from app.api.media import resolve_media_path
from app.config import get_settings
from app.db import get_db, release
from app.models import Folder, Job, SavedFilter, utcnow
from app.schemas import (
    BatchIn, BatchOut, FavouriteIn, FavouriteOut, FavouritesImportIn, FavouritesOut, FolderIn, FolderOut, FolderPatch,
    MoveIn, SavedFilterIn, SavedFilterOut, SavedFilterPatch,
)
from app.security import CurrentUser, get_current_user, require_editor
from app.services import job_out

router = APIRouter(tags=["organise"])

MAX_FILTER_BYTES = 4000


def _err(e: org.OrganiseError, code: int = 422):
    return HTTPException(404 if "not found" in str(e).lower() else code, str(e))


def _folder_out(f: Folder, counts: dict[str, int]) -> FolderOut:
    return FolderOut.model_validate(f).model_copy(update={"item_count": counts.get(f.id, 0)})


def _owned(db: Session, cur: CurrentUser, folder_id: str) -> Folder:
    try:
        return org.owned_folder(db, cur.workspace_id, folder_id)
    except org.OrganiseError as e:
        raise _err(e) from None


# ---------------------------------------------------------------- folders

@router.get("/folders", response_model=list[FolderOut])
def list_folders(kind: Literal["image", "video"] | None = None, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(get_current_user)):
    want = vis.wanted(cur)
    q = select(Folder).where(Folder.workspace_id == cur.workspace_id, *vis.where(Folder, want))
    if kind:
        q = q.where(Folder.kind.in_(("any", kind)))
    counts = org.item_counts(db, cur.workspace_id, want)
    return [_folder_out(f, counts) for f in db.scalars(q.order_by(Folder.sort, Folder.name))]


@router.post("/folders", response_model=FolderOut, status_code=201)
def create_folder(body: FolderIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    try:
        parent = org.check_parent(db, cur.workspace_id, None, body.parent_id)
    except org.OrganiseError as e:
        raise _err(e) from None
    # a sub-folder can't be broader than its parent
    kind = parent.kind if parent is not None and parent.kind != "any" else body.kind
    f = Folder(workspace_id=cur.workspace_id, name=body.name.strip(), parent_id=parent.id if parent else None,
               kind=kind)
    db.add(f)
    db.commit()
    return _folder_out(f, {})


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def patch_folder(folder_id: str, body: FolderPatch, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    f = _owned(db, cur, folder_id)
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(422, "The name can't be blank")
        f.name = body.name.strip()
    if "parent_id" in body.model_fields_set:
        try:
            parent = org.check_parent(db, cur.workspace_id, f, body.parent_id or None)
        except org.OrganiseError as e:
            raise _err(e) from None
        f.parent_id = parent.id if parent else None
    if body.sort is not None:
        f.sort = body.sort
    f.updated_at = utcnow()
    db.commit()
    return _folder_out(f, org.item_counts(db, cur.workspace_id))


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    org.delete_folder(db, _owned(db, cur, folder_id))
    db.commit()
    return Response(status_code=204)


@router.post("/media/move", response_model=BatchOut)
def move_media(body: MoveIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    return _move(db, cur, body.refs, body.folder_id)


def _move(db: Session, cur: CurrentUser, refs: list[str], folder_id: str | None) -> BatchOut:
    folder = _owned(db, cur, folder_id) if folder_id else None
    found, skipped = org.resolve_many(db, cur.workspace_id, refs)
    moved, more = org.move(db, cur.workspace_id, found, folder)
    db.commit()
    return BatchOut(action="move", done=moved, skipped=skipped + more)


# ---------------------------------------------------------------- favourites

@router.get("/favourites", response_model=FavouritesOut)
def list_favourites(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return FavouritesOut(refs=sorted(org.favourite_refs(db, cur.id, cur.workspace_id)))


@router.post("/favourites/toggle", response_model=FavouriteOut)
def toggle_favourite(body: FavouriteIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    r = org.resolve(db, cur.workspace_id, body.ref)
    if r is None:
        raise HTTPException(404, "Media not found")
    on = body.on if body.on is not None else r.ref not in org.favourite_refs(db, cur.id, cur.workspace_id)
    org.set_favourite(db, cur.id, cur.workspace_id, r.ref, on)
    db.commit()
    return FavouriteOut(ref=r.ref, favourite=on)


@router.post("/favourites/import", response_model=FavouritesOut)
def import_favourites(body: FavouritesImportIn, db: Session = Depends(get_db),
                      cur: CurrentUser = Depends(get_current_user)):
    """One-off hand-over of the hearts the old browser-only store kept. Unknown ids are dropped quietly."""
    have = org.favourite_refs(db, cur.id, cur.workspace_id)
    imported = skipped = 0
    for raw in body.refs[:2000]:
        r = org.resolve(db, cur.workspace_id, raw)
        if r is None:
            skipped += 1
        elif r.ref not in have:
            org.set_favourite(db, cur.id, cur.workspace_id, r.ref, True)
            have.add(r.ref)
            imported += 1
    db.commit()
    return FavouritesOut(refs=sorted(have), imported=imported, skipped=skipped)


# ---------------------------------------------------------------- saved filters

def _filter_query(q: dict) -> dict:
    import json

    if len(json.dumps(q, default=str)) > MAX_FILTER_BYTES:
        raise HTTPException(422, "That filter is too big to save")
    return q


def _owned_filter(db: Session, cur: CurrentUser, filter_id: str) -> SavedFilter:
    f = db.get(SavedFilter, filter_id)
    if f is None or f.user_id != cur.id or f.workspace_id != cur.workspace_id:
        raise HTTPException(404, "Saved filter not found")
    return f


@router.get("/saved-filters", response_model=list[SavedFilterOut])
def list_saved_filters(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return db.scalars(select(SavedFilter).where(SavedFilter.user_id == cur.id,
                                                SavedFilter.workspace_id == cur.workspace_id)
                      .order_by(SavedFilter.created_at)).all()


@router.post("/saved-filters", response_model=SavedFilterOut, status_code=201)
def create_saved_filter(body: SavedFilterIn, db: Session = Depends(get_db),
                        cur: CurrentUser = Depends(get_current_user)):
    f = SavedFilter(workspace_id=cur.workspace_id, user_id=cur.id, name=body.name.strip(),
                    query=_filter_query(body.query))
    db.add(f)
    db.commit()
    return f


@router.patch("/saved-filters/{filter_id}", response_model=SavedFilterOut)
def patch_saved_filter(filter_id: str, body: SavedFilterPatch, db: Session = Depends(get_db),
                       cur: CurrentUser = Depends(get_current_user)):
    f = _owned_filter(db, cur, filter_id)
    if body.name is not None:
        f.name = body.name.strip()
    if body.query is not None:
        f.query = _filter_query(body.query)
    f.updated_at = utcnow()
    db.commit()
    return f


@router.delete("/saved-filters/{filter_id}", status_code=204)
def delete_saved_filter(filter_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    db.delete(_owned_filter(db, cur, filter_id))
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- batch

def _upscale_choice(options: dict, kind: str) -> tuple[str | None, str | None]:
    per = options.get(kind) if isinstance(options.get(kind), dict) else {}
    return per.get("engine") or None, per.get("target") or None


@router.post("/media/batch", response_model=BatchOut)
def media_batch(body: BatchIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    opts = body.options or {}
    if body.action == "move":
        fid = opts.get("folder_id")
        return _move(db, cur, body.refs, fid if isinstance(fid, str) and fid else None)
    try:
        found, skipped = org.resolve_many(db, cur.workspace_id, body.refs)
    except org.OrganiseError as e:
        raise _err(e) from None
    out = BatchOut(action=body.action, skipped=skipped)
    project_only = "Project results are changed inside their project"

    if body.action == "delete":
        gone = []
        for r in found:
            if not r.standalone:
                out.skipped.append({"ref": r.ref, "reason": project_only})
                continue
            if lib.in_use(db, r.item):
                out.skipped.append({"ref": r.ref, "reason": lib.IN_USE})
                continue
            lib.delete_item(db, r.item)
            gone.append(r.ref)
        org.forget(db, cur.workspace_id, gone)
        out.done = gone
    elif body.action == "tag":
        add = lib.clean_tags(opts.get("add") or [])
        remove = set(lib.clean_tags(opts.get("remove") or []))
        if not add and not remove:
            raise HTTPException(422, "Say which tags to add or remove")
        for r in found:
            if not r.standalone:
                out.skipped.append({"ref": r.ref, "reason": project_only})
                continue
            tags = [t for t in (r.item.tags or []) if t not in remove]
            r.item.tags = lib.clean_tags(tags + add)[:30]
            lib.touch(r.item)
            out.done.append(r.ref)
    elif body.action == "upscale":
        for r in found:
            g = org.current_generation(db, r)
            if g is None:
                out.skipped.append({"ref": r.ref, "reason": "Nothing to upscale yet"})
                continue
            engine, target = _upscale_choice(opts, r.kind)
            try:
                # both queue_* functions validate before they add anything, so a skip leaves no rows behind
                if upscale_image.is_image(g):
                    job = upscale_image.queue_image_upscale(db, g, engine, target, user_id=cur.id)
                else:
                    job = upscale.queue_upscale(db, g, engine, target or "1080p", user_id=cur.id)
            except upscale.UpscaleError as e:
                out.skipped.append({"ref": r.ref, "reason": str(e)})
                continue
            out.done.append(r.ref)
            out.jobs.append(job_out(job))
    elif body.action == "download":
        try:
            job, more = org.queue_zip(db, cur.workspace_id, found, cur.id)
        except org.OrganiseError as e:
            raise HTTPException(422, str(e)) from None
        out.skipped += more
        out.done = [r.ref for r in found if r.ref not in {s["ref"] for s in more}]
        out.jobs = [job_out(job)]
    db.commit()
    return out


@router.get("/exports/{job_id}/download")
def download_export(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    job = db.get(Job, job_id)
    if job is None or job.workspace_id != cur.workspace_id or job.type != org.ZIP_JOB:
        raise HTTPException(404, "Export not found")
    if job.status != "done":
        raise HTTPException(409, "The zip isn't ready yet")
    path = resolve_media_path((job.result or {}).get("file") or "", cur.workspace_id, get_settings().data_dir)
    if path is None:
        raise HTTPException(410, "This zip has been cleared; make it again from the Library")
    name = f"mixai-library-{job.created_at:%Y%m%d-%H%M}.zip"
    # FileResponse streams in chunks, so a multi-GB archive never sits in memory
    release(db)
    return FileResponse(path, media_type="application/zip", filename=name,
                        headers={"Cache-Control": "private, no-store"})
