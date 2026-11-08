from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import thumbs
from app.config import get_settings
from app.db import get_db, release
from app.models import Generation
from app.moderation import removed_generation, removed_path
from app.security import CurrentUser, get_current_user

router = APIRouter(tags=["media"])

AUDIO_TYPES = {".flac": "audio/flac", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
               ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".weba": "audio/webm",
               # mimetypes has no entry for glTF on Windows or most Linux boxes
               ".glb": "model/gltf-binary", ".gltf": "model/gltf+json"}


def resolve_media_path(rel: str, workspace_id: str, data_dir: Path) -> Path | None:
    if not rel or "\x00" in rel:
        return None
    root = data_dir.resolve()
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    # tenancy: you only ever see files under your own workspace folder
    ws_root = root / "workspaces" / workspace_id
    if not target.is_relative_to(ws_root) or not target.is_file():
        return None
    return target


# must stay above /media/{path:path}, which would otherwise swallow it
@router.get("/media/thumb/{gen_id}")
def get_thumb(gen_id: str, w: int = Query(thumbs.DEFAULT), db: Session = Depends(get_db),
              cur: CurrentUser = Depends(get_current_user)):
    if w not in thumbs.SIZES:
        raise HTTPException(422, f"w must be one of {', '.join(map(str, thumbs.SIZES))}")
    g = db.get(Generation, gen_id)
    if g is None or g.workspace_id != cur.workspace_id or thumbs.url_for(g) is None or removed_generation(db, g):
        raise HTTPException(404, "Not found")
    data_dir = get_settings().data_dir
    src = resolve_media_path(g.file_path, cur.workspace_id, data_dir)
    dest = thumbs.ensure(src, g.media_type, w) if src is not None else None
    if dest is None and src is not None and (g.media_type or "").startswith("model/") and thumbs.model_thumbs(g):
        dest = thumbs.ensure(src, g.media_type, w)
    if dest is None or not dest.resolve().is_relative_to(data_dir.resolve() / "workspaces" / cur.workspace_id):
        raise HTTPException(404, "No preview for this file")
    # a generation's file never changes, so neither does its thumb
    kind = "image/png" if dest.suffix == ".png" else "image/webp"  # audio: the waveform
    release(db)
    return FileResponse(dest, media_type=kind, headers={"Cache-Control": "private, max-age=604800"})


@router.get("/media/{path:path}")
def get_media(path: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    target = resolve_media_path(path, cur.workspace_id, get_settings().data_dir)
    if target is None or removed_path(db, cur.workspace_id, path):
        raise HTTPException(404, "Not found")
    release(db)
    # mimetypes doesn't know .flac / .m4a everywhere; <audio> wants the real type to start playing
    return FileResponse(target, media_type=AUDIO_TYPES.get(target.suffix.lower()),
                        headers={"Cache-Control": "private, max-age=3600"})
