"""Signed download links (app.file_links). No session or token: the signature is the permission."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import file_links, reel
from app.api.media import resolve_media_path
from app.config import get_settings
from app.db import get_db, release
from app.models import Generation, Project
from app.moderation import removed_generation
from app.services import READY_STATES

router = APIRouter(tags=["generations"])


@router.get("/files/{signed}")
def signed_download(signed: str, db: Session = Depends(get_db)):
    found = file_links.read(signed)
    g = db.get(Generation, found[0]) if found else None
    # one answer for expired, tampered and gone, so a link can't be probed
    if g is None or g.workspace_id != found[1] or g.status not in READY_STATES or removed_generation(db, g):
        raise HTTPException(404, "This link has expired or the file is gone")
    path = resolve_media_path(g.file_path or "", g.workspace_id, get_settings().data_dir)
    if path is None:
        raise HTTPException(404, "This link has expired or the file is gone")
    name = reel.download_name(db.get(Project, g.project_id) if g.project_id else None, g)
    release(db)
    return FileResponse(path, media_type=g.media_type or None, filename=name,
                        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"})
