"""Library organisation (polish P4): folders, favourites, refs, batch actions and the zip export job.

A "ref" names one Library row. Standalone items are their MediaItem id; project results (renders and
approved stills, which are never copied into media_item) are "gen:<generation id>". Bare generation ids
are accepted too and normalised, so favourites saved by the old browser-only store still resolve.
"""
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import visibility as vis
from app import library as lib
from app.config import get_settings
from app.models import Favourite, Folder, FolderLink, Generation, Job, MediaItem, utcnow
from app.schemas import MediaItemOut

log = logging.getLogger("mixai.organise")

ZIP_JOB = "media_zip"
MAX_REFS = 500
MAX_DEPTH = 6
GEN = "gen:"


class OrganiseError(ValueError):
    pass


@dataclass
class Ref:
    ref: str  # canonical
    kind: str  # image | video | audio
    item: MediaItem | None = None
    gen: Generation | None = None

    @property
    def standalone(self) -> bool:
        return self.item is not None


def _project_result(g: Generation | None, workspace_id: str) -> bool:
    return (g is not None and g.workspace_id == workspace_id and g.target_type != "media" and g.project_id is not None
            and g.status in lib.FINISHED and bool(g.file_path)
            and (g.kind == "render" or g.kind in lib.PROJECT_IMAGE_KINDS))


def resolve(db: Session, workspace_id: str, raw: str) -> Ref | None:
    raw = (raw or "").strip()
    if not raw or len(raw) > 60:
        return None
    gid = raw[len(GEN):] if raw.startswith(GEN) else None
    if gid is None:
        item = db.get(MediaItem, raw)
        if item is not None and item.workspace_id == workspace_id:
            return Ref(item.id, item.kind, item=item)
        gid = raw
    g = db.get(Generation, gid)
    if not _project_result(g, workspace_id):
        return None
    return Ref(GEN + g.id, "video" if g.kind == "render" else "image", gen=g)


def resolve_many(db: Session, workspace_id: str, refs: list[str]) -> tuple[list[Ref], list[dict]]:
    if len(refs) > MAX_REFS:
        raise OrganiseError(f"At most {MAX_REFS} items at a time")
    found: list[Ref] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for raw in refs:
        r = resolve(db, workspace_id, raw)
        if r is None:
            skipped.append({"ref": raw, "reason": "Not found"})
        elif r.ref not in seen:
            seen.add(r.ref)
            found.append(r)
    return found, skipped


def ref_of(row: MediaItemOut) -> str:
    return GEN + row.id if row.origin == "project" else row.id


# ---------------------------------------------------------------- folders

def owned_folder(db: Session, workspace_id: str, folder_id: str | None) -> Folder | None:
    if not folder_id:
        return None
    f = db.get(Folder, folder_id)
    if f is None or f.workspace_id != workspace_id:
        raise OrganiseError("Folder not found")
    return f


def ancestors(db: Session, f: Folder) -> list[Folder]:
    out, seen = [], {f.id}
    cur = f
    while cur.parent_id and cur.parent_id not in seen:
        cur = db.get(Folder, cur.parent_id)
        if cur is None:
            break
        seen.add(cur.id)
        out.append(cur)
    return out


def check_parent(db: Session, workspace_id: str, folder: Folder | None, parent_id: str | None) -> Folder | None:
    parent = owned_folder(db, workspace_id, parent_id)
    if parent is None:
        return None
    chain = [parent, *ancestors(db, parent)]
    if folder is not None and any(p.id == folder.id for p in chain):
        raise OrganiseError("A folder can't go inside itself")
    if len(chain) >= MAX_DEPTH:
        raise OrganiseError(f"Folders nest at most {MAX_DEPTH} deep")
    return parent


def new_folder(db: Session, workspace_id: str, name: str, kind: str = "any", parent: Folder | None = None) -> Folder:
    # a sub-folder can't be broader than its parent
    if parent is not None and parent.kind != "any":
        kind = parent.kind
    f = Folder(workspace_id=workspace_id, name=name.strip(), parent_id=parent.id if parent else None, kind=kind)
    db.add(f)
    db.flush()
    return f


def item_counts(db: Session, workspace_id: str, want: bool | None = None) -> dict[str, int]:
    counts: dict[str, int] = dict(db.execute(
        select(MediaItem.folder_id, func.count()).where(
            MediaItem.workspace_id == workspace_id, MediaItem.folder_id.is_not(None), *vis.where(MediaItem, want),
            MediaItem.kind.in_(("image", "video", "audio"))).group_by(MediaItem.folder_id)).all())
    for fid, n in db.execute(select(FolderLink.folder_id, func.count()).where(
            FolderLink.workspace_id == workspace_id).group_by(FolderLink.folder_id)):
        counts[fid] = counts.get(fid, 0) + n
    return counts


def delete_folder(db: Session, f: Folder) -> None:
    """Contents (items, links, sub-folders) move up to the parent; nothing in the library is deleted."""
    to = f.parent_id
    db.execute(update(Folder).where(Folder.parent_id == f.id).values(parent_id=to, updated_at=utcnow()))
    db.execute(update(MediaItem).where(MediaItem.folder_id == f.id).values(folder_id=to))
    if to:
        db.execute(update(FolderLink).where(FolderLink.folder_id == f.id).values(folder_id=to))
    else:
        db.execute(delete(FolderLink).where(FolderLink.folder_id == f.id))
    db.delete(f)
    db.flush()


def move(db: Session, workspace_id: str, refs: list[Ref], folder: Folder | None) -> tuple[list[str], list[dict]]:
    moved, skipped = [], []
    for r in refs:
        if folder is not None and folder.kind != "any" and folder.kind != r.kind:
            skipped.append({"ref": r.ref, "reason": f"“{folder.name}” only holds {folder.kind}s"})
            continue
        if r.item is not None:
            r.item.folder_id = folder.id if folder else None
        else:
            link = db.scalar(select(FolderLink).where(FolderLink.workspace_id == workspace_id,
                                                      FolderLink.media_ref == r.ref))
            if folder is None:
                if link is not None:
                    db.delete(link)
            elif link is None:
                db.add(FolderLink(workspace_id=workspace_id, folder_id=folder.id, media_ref=r.ref))
            else:
                link.folder_id = folder.id
        moved.append(r.ref)
    db.flush()
    return moved, skipped


# ---------------------------------------------------------------- favourites

def favourite_refs(db: Session, user_id: str, workspace_id: str) -> set[str]:
    return set(db.scalars(select(Favourite.media_ref).where(Favourite.user_id == user_id,
                                                            Favourite.workspace_id == workspace_id)))


def set_favourite(db: Session, user_id: str, workspace_id: str, ref: str, on: bool) -> None:
    row = db.scalar(select(Favourite).where(Favourite.user_id == user_id, Favourite.media_ref == ref))
    if on and row is None:
        db.add(Favourite(user_id=user_id, workspace_id=workspace_id, media_ref=ref))
    elif not on and row is not None:
        db.delete(row)
    db.flush()


def scope(db: Session, workspace_id: str, user_id: str, folder_id: str | None, favourites: bool) -> lib.Scope | None:
    if not folder_id and not favourites:
        return None
    s = lib.Scope()
    if folder_id:
        s.folder_id = folder_id
        s.gen_ids = {r[len(GEN):] for r in db.scalars(select(FolderLink.media_ref).where(
            FolderLink.workspace_id == workspace_id, FolderLink.folder_id == folder_id))}
    if favourites:
        favs = favourite_refs(db, user_id, workspace_id)
        s.media_ids = {r for r in favs if not r.startswith(GEN)}
        fav_gens = {r[len(GEN):] for r in favs if r.startswith(GEN)}
        s.gen_ids = fav_gens if s.gen_ids is None else s.gen_ids & fav_gens
    return s


def annotate(db: Session, workspace_id: str, user_id: str, rows: list[MediaItemOut]) -> list[MediaItemOut]:
    """Per-user favourite flag, and the folder of project rows (standalone items carry their own)."""
    if not rows:
        return rows
    favs = favourite_refs(db, user_id, workspace_id)
    gen_refs = [GEN + r.id for r in rows if r.origin == "project"]
    links = dict(db.execute(select(FolderLink.media_ref, FolderLink.folder_id).where(
        FolderLink.workspace_id == workspace_id, FolderLink.media_ref.in_(gen_refs))).all()) if gen_refs else {}
    for r in rows:
        ref = ref_of(r)
        r.favourite = ref in favs
        if r.origin == "project":
            r.folder_id = links.get(ref)
    return rows


def forget(db: Session, workspace_id: str, refs: list[str]) -> None:
    if refs:
        db.execute(delete(Favourite).where(Favourite.workspace_id == workspace_id, Favourite.media_ref.in_(refs)))
        db.execute(delete(FolderLink).where(FolderLink.workspace_id == workspace_id, FolderLink.media_ref.in_(refs)))


# ---------------------------------------------------------------- batch

def current_generation(db: Session, r: Ref) -> Generation | None:
    if r.gen is not None:
        return r.gen
    return db.get(Generation, r.item.generation_id) if r.item and r.item.generation_id else None


def queue_zip(db: Session, workspace_id: str, refs: list[Ref], user_id: str) -> tuple[Job, list[dict]]:
    from app.reel import safe_name

    files, skipped, names = [], [], set()
    for r in refs:
        g = current_generation(db, r)
        if g is None or g.status not in lib.FINISHED or not g.file_path:
            skipped.append({"ref": r.ref, "reason": "Not finished yet"})
            continue
        if r.item is not None:
            title = r.item.title
        else:
            row = lib.project_row(db, workspace_id, g.id)
            title = row[1].title if row else (g.params or {}).get("title") or g.kind
        base = safe_name(title or "", "media")
        ext = Path(g.file_path).suffix
        name, n = f"{base}{ext}", 2
        while name.lower() in names:
            name, n = f"{base}-{n}{ext}", n + 1
        names.add(name.lower())
        files.append({"path": g.file_path, "name": name})
    if not files:
        raise OrganiseError("None of these items has a finished file to download")
    # it's a quick CPU job, so it jumps the GPU queue
    job = Job(workspace_id=workspace_id, type=ZIP_JOB, priority=50, message="Waiting for a worker",
              payload={"files": files, "user_id": user_id})
    db.add(job)
    db.flush()
    return job, skipped


def exports_dir(workspace_id: str) -> Path:
    return get_settings().data_dir / "workspaces" / workspace_id / "exports"


def handle_zip(ctx) -> dict:
    job = ctx.job
    files = (job.payload or {}).get("files") or []
    root = get_settings().data_dir.resolve()
    ws_root = root / "workspaces" / job.workspace_id
    out_dir = exports_dir(job.workspace_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{job.id}.zip"
    part = dest.with_suffix(".zip.part")
    added, missing = 0, []
    # TODO: prune exports older than a day; for now they stay until the workspace folder is cleaned
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for i, f in enumerate(files):
            src = (root / f["path"]).resolve()
            if not src.is_relative_to(ws_root) or not src.is_file():
                missing.append(f["name"])
                continue
            # pictures and mp4s are already compressed; storing them is as small and far quicker
            zf.write(src, arcname=f["name"])
            added += 1
            ctx.progress((i + 1) / max(1, len(files)) * 0.98, f"Zipping {i + 1} of {len(files)}")
    if not added:
        part.unlink(missing_ok=True)
        raise RuntimeError("None of the files exist any more")
    part.replace(dest)
    rel = dest.relative_to(root).as_posix()
    return {"file": rel, "bytes": dest.stat().st_size, "count": added, "missing": missing,
            "download_url": f"/api/exports/{job.id}/download"}
