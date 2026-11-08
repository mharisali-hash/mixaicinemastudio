"""The Library behind the Image and Video sections (contract v5).

Standalone results are MediaItems with their own generations (target_type "media"). Project results
are read through: finished renders (newest per title) and approved stills are turned into the same
MediaItem shape on the fly, keyed by their generation id, so nothing is copied or kept in sync.
"""
import base64
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image
from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.orm import Session

from app import styles, thumbs
from app import visibility as vis
from app.config import get_settings
from app.models import Character, Generation, Job, Location, MediaItem, Project, utcnow
from app.schemas import MediaDetailOut, MediaItemOut
from app.services import READY_STATES, gen_out, media_url

log = logging.getLogger("mixai.library")

FINISHED = ("ready", "approved")
PROJECT_IMAGE_KINDS = ("portrait", "establishing", "keyframe_start", "keyframe_mid", "keyframe_end")
KIND_LABELS = {"portrait": "Portrait", "establishing": "Establishing", "keyframe_start": "Start frame",
               "keyframe_mid": "Mid frame", "keyframe_end": "End frame"}
MAX_LIMIT = 100
TAKE_TAG = "script:"
# working files hidden like dialogue takes: a shot's spoken lines and a character test clip's line (contract v26)
SHOT_TAG = "shot:"
CLIP_TAG = "clip:"
HIDDEN_TAGS = (TAKE_TAG, SHOT_TAG, CLIP_TAG)


@dataclass
class Scope:
    """Narrows a listing to a folder and/or favourites (P4). None on a field means no restriction."""
    folder_id: str | None = None
    media_ids: set[str] | None = None
    gen_ids: set[str] | None = None


def media_dir(workspace_id: str) -> tuple[Path, str]:
    rel = Path("workspaces") / workspace_id / "media"
    return get_settings().data_dir / rel, rel.as_posix()


def short_title(text: str, words: int = 7) -> str:
    parts = " ".join(text.split()).split(" ")
    out = " ".join(parts[:words])[:80].rstrip(" ,.;:")
    return (out + ("…" if len(parts) > words else "")) or "Untitled"


def home_of(value: str | None) -> str | None:
    """A caller's part_of label as stored on MediaItem.home (contract v37); blank means a final output."""
    v = " ".join((value or "").split())[:120]
    return v or None


def clean_tags(tags: list[str]) -> list[str]:
    seen: list[str] = []
    for t in tags:
        t = " ".join(str(t).split()).lower().replace('"', "")[:40]
        if t and t not in seen:
            seen.append(t)
    return seen


# ---------------------------------------------------------------- sizes and styles

ASPECTS = {"1:1": (1, 1), "16:9": (16, 9), "9:16": (9, 16), "4:3": (4, 3), "3:4": (3, 4), "2:3": (2, 3), "3:2": (3, 2)}
TARGET_PIXELS = 1024 * 1024

# the style catalogue (contract v31) lives in templates/styles.json; aliases (painterly -> oil) included here
STYLES = styles.by_id()


def snap64(x: float) -> int:
    return max(64, int(round(x / 64)) * 64)


def size_for_ratio(rw: float, rh: float) -> tuple[int, int]:
    w = math.sqrt(TARGET_PIXELS * rw / rh)
    return snap64(w), snap64(w * rh / rw)


def size_for_aspect(aspect: str) -> tuple[int, int]:
    return size_for_ratio(*ASPECTS[aspect])


def styled_prompt(prompt: str, style: str | None) -> str:
    s = styles.get(style)
    if s is None:
        return prompt.strip()
    if not prompt.strip():
        return s.positive  # a restyle may say nothing but the style
    return f"{prompt.strip().rstrip('.')}. {s.positive}"


# ---------------------------------------------------------------- shaping

def _cursor(at: datetime, id_: str) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{id_}".encode()).decode().rstrip("=")


def parse_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        at, id_ = raw.split("|", 1)
        return datetime.fromisoformat(at), id_
    except (ValueError, UnicodeDecodeError):
        return None


def _version_counts(db: Session, item_ids: list[str]) -> dict[str, int]:
    if not item_ids:
        return {}
    rows = db.execute(select(Generation.target_id, func.count()).where(
        Generation.target_type == "media", Generation.target_id.in_(item_ids), Generation.status != "rejected",
    ).group_by(Generation.target_id)).all()
    return dict(rows)


UPSCALE_LABELS = {"2x": "2×", "4x": "4×", "2k": "2K", "4k": "4K", "1080p": "1080p", "1440p": "1440p"}


def upscale_badge(g: Generation | None) -> dict | None:
    up = (g.params or {}).get("upscale") if g is not None else None
    if not isinstance(up, dict) or not up.get("target"):
        return None
    t = str(up["target"])
    return {"target": t, "label": UPSCALE_LABELS.get(t, t.upper()), "engine": up.get("engine")}


def originals(db: Session, gens: list[Generation]) -> dict[str, str]:
    """generation id -> the root of its parent chain (the upload or first picture), for versions that have one."""
    up: dict[str, str | None] = {g.id: g.parent_id for g in gens}
    frontier = {p for p in up.values() if p and p not in up}
    for _ in range(6):  # chains are short (upload -> upscale -> regenerate); stop on anything silly
        if not frontier:
            break
        rows = db.execute(select(Generation.id, Generation.parent_id).where(Generation.id.in_(frontier))).all()
        up.update({r[0]: r[1] for r in rows})
        frontier = {r[1] for r in rows if r[1] and r[1] not in up}
    out = {}
    for g in gens:
        cur, seen = g.parent_id, {g.id}
        while cur and up.get(cur) and cur not in seen:
            seen.add(cur)
            cur = up[cur]
        if cur and cur in up:
            out[g.id] = cur
    return out


def items_out(db: Session, items: list[MediaItem]) -> list[MediaItemOut]:
    gen_ids = [i.generation_id for i in items if i.generation_id]
    gens = {g.id: g for g in db.scalars(select(Generation).where(Generation.id.in_(gen_ids)))} if gen_ids else {}
    counts = _version_counts(db, [i.id for i in items])
    pids = {i.project_id for i in items if i.project_id}
    ptitles = dict(db.execute(select(Project.id, Project.title).where(Project.id.in_(pids))).all()) if pids else {}
    roots = originals(db, list(gens.values()))
    from app.audio_analysis import summary as audio_summary
    from app.catalogue.albums import memberships

    albums = memberships(db, [i.id for i in items])
    out = []
    for i in items:
        g = gens.get(i.generation_id)
        url = media_url(g.file_path) if g is not None and g.status in READY_STATES else None
        out.append(MediaItemOut(
            id=i.id, workspace_id=i.workspace_id, kind=i.kind, origin=i.origin, title=i.title, tags=list(i.tags or []),
            project_id=i.project_id, project_title=ptitles.get(i.project_id), generation_id=i.generation_id,
            status=g.status if g is not None else None, media_type=g.media_type if g is not None else None,
            width=i.width, height=i.height, duration_s=i.duration_s, media_url=url,
            thumb_url=thumbs.url_for(g),
            created_at=i.created_at, updated_at=i.updated_at, versions_count=counts.get(i.id, 1),
            folder_id=i.folder_id, upscale=upscale_badge(g),
            original_generation_id=roots.get(i.generation_id) if g is not None else None,
            rating=i.rating or 0, flag=i.flag or "", label=i.label or "", caption=i.caption or "",
            captured_at=i.captured_at, camera=i.camera, lens=i.lens, focal_mm=i.focal_mm, aperture=i.aperture,
            shutter_s=i.shutter_s, iso=i.iso, original_name=i.original_name, bytes=i.bytes,
            source_type=i.source_type, edited=counts.get(i.id, 1) > 1, album_ids=albums.get(i.id, []),
            analysis=audio_summary(g) if i.kind == "audio" else None,
            params=public_params(g), home=i.home,
        ))
    return out


def public_params(g: Generation | None) -> dict | None:
    if g is None or not g.params:
        return None
    p = {k: v for k, v in g.params.items() if k != "created_by"}
    if isinstance(p.get("voice"), dict):
        # the reference clip's server path stays on the server
        p["voice"] = {k: v for k, v in p["voice"].items() if k != "ref_file"}
    if g.seed is not None:
        p.setdefault("seed", g.seed)
    return p


def item_out(db: Session, item: MediaItem) -> MediaItemOut:
    return items_out(db, [item])[0]


def _image_dims(g: Generation) -> tuple[int | None, int | None]:
    size = (g.params or {}).get("size")
    if isinstance(size, list) and len(size) == 2:
        return int(size[0]), int(size[1])
    path = get_settings().data_dir / g.file_path if g.file_path else None
    try:
        with Image.open(path) as im:  # header only
            return im.size
    except (OSError, TypeError):
        return None, None


def _project_rows(db: Session, workspace_id: str, *, kind: str | None, project_id: str | None,
                  only_ids: list[str] | None = None, want: bool | None = None) -> list[tuple[Generation, MediaItemOut]]:
    """Read-through rows for project results. Newest render per (project, title); approved stills."""
    rows: list[tuple[Generation, MediaItemOut]] = []
    base = [Generation.workspace_id == workspace_id, Generation.target_type != "media",
            Generation.project_id.is_not(None), *vis.where(Generation, want)]
    if project_id:
        base.append(Generation.project_id == project_id)
    if only_ids is not None:
        base.append(Generation.id.in_(only_ids))

    renders: list[Generation] = []
    if kind in (None, "video"):
        q = select(Generation).where(*base, Generation.kind == "render", Generation.target_type == "project",
                                     Generation.status.in_(FINISHED), Generation.file_path.is_not(None))
        latest: dict[tuple[str, str], Generation] = {}
        for g in db.scalars(q.order_by(Generation.created_at.desc())):
            key = (g.project_id, (g.params or {}).get("title") or "Full film")
            latest.setdefault(key, g)
        renders = list(latest.values())
    stills: list[Generation] = []
    if kind in (None, "image"):
        stills = list(db.scalars(select(Generation).where(
            *base, Generation.kind.in_(PROJECT_IMAGE_KINDS), Generation.status == "approved",
            Generation.file_path.is_not(None))))
    if not renders and not stills:
        return rows

    pids = {g.project_id for g in renders + stills}
    projects = dict(db.execute(select(Project.id, Project.title).where(Project.id.in_(pids))).all())
    names: dict[str, str] = {}
    chars = [g.target_id for g in stills if g.target_type == "character"]
    locs = [g.target_id for g in stills if g.target_type == "location"]
    if chars:
        names.update(db.execute(select(Character.id, Character.name).where(Character.id.in_(chars))).all())
    if locs:
        names.update(db.execute(select(Location.id, Location.name).where(Location.id.in_(locs))).all())

    # versions_count = siblings in the same version line
    title_counts: dict[tuple[str, str], int] = {}
    if renders:
        for pid, params in db.execute(select(Generation.project_id, Generation.params).where(
                Generation.project_id.in_({g.project_id for g in renders}), Generation.kind == "render",
                Generation.target_type == "project", Generation.status.in_(FINISHED))):
            key = (pid, (params or {}).get("title") or "Full film")
            title_counts[key] = title_counts.get(key, 0) + 1
    still_counts: dict[tuple[str, str], int] = {}
    if stills:
        still_counts = {(t, k): n for t, k, n in db.execute(
            select(Generation.target_id, Generation.kind, func.count()).where(
                Generation.target_id.in_({g.target_id for g in stills}), Generation.kind.in_(PROJECT_IMAGE_KINDS),
                Generation.status != "rejected").group_by(Generation.target_id, Generation.kind))}

    roots = originals(db, renders + stills)
    for g in renders:
        p = g.params or {}
        title = p.get("title") or "Full film"
        size = p.get("size") if isinstance(p.get("size"), list) else None
        rows.append((g, MediaItemOut(
            id=g.id, workspace_id=g.workspace_id, kind="video", origin="project", title=title, tags=[],
            project_id=g.project_id, project_title=projects.get(g.project_id), generation_id=g.id, status=g.status,
            media_type=g.media_type, width=size[0] if size else None, height=size[1] if size else None,
            duration_s=p.get("duration_s"), media_url=media_url(g.file_path), thumb_url=thumbs.url_for(g),
            created_at=g.created_at, updated_at=g.updated_at,
            versions_count=title_counts.get((g.project_id, title), 1),
            upscale=upscale_badge(g), original_generation_id=roots.get(g.id),
        )))
    for g in stills:
        w, h = _image_dims(g)
        owner = names.get(g.target_id) or projects.get(g.project_id) or ""
        title = f"{owner} · {KIND_LABELS[g.kind]}" if owner else KIND_LABELS[g.kind]
        url = media_url(g.file_path)
        rows.append((g, MediaItemOut(
            id=g.id, workspace_id=g.workspace_id, kind="image", origin="project", title=title, tags=[],
            project_id=g.project_id, project_title=projects.get(g.project_id), generation_id=g.id, status=g.status,
            media_type=g.media_type, width=w, height=h, media_url=url, thumb_url=thumbs.url_for(g),
            created_at=g.created_at, updated_at=g.updated_at,
            versions_count=still_counts.get((g.target_id, g.kind), 1),
            upscale=upscale_badge(g), original_generation_id=roots.get(g.id),
        )))
    return rows


def list_media(db: Session, workspace_id: str, *, kind: str | None = None, origin: str | None = None,
               q: str | None = None, tag: str | None = None, project_id: str | None = None,
               include_project: bool = False, limit: int = 40, cursor: str | None = None,
               scope: Scope | None = None, want: bool | None = None, home: str | None = None,
               include_home: list[str] | None = None) -> tuple[list[MediaItemOut], str | None]:
    """`want` is app.visibility.wanted(): False keeps API-made rows out of the studio's own Library.

    Working parts of a studio (home set, contract v37) are left out unless asked for: `home` lists only
    that studio's parts ("any" for every part), `include_home` adds those studios' parts to the normal list."""
    limit = max(1, min(MAX_LIMIT, limit))
    after = parse_cursor(cursor)
    needle = (q or "").strip().lower()
    merged: list[tuple[datetime, str, MediaItemOut | MediaItem]] = []

    if origin in (None, "generated", "upload"):
        stmt = select(MediaItem).where(MediaItem.workspace_id == workspace_id, *vis.where(MediaItem, want))
        if kind:
            stmt = stmt.where(MediaItem.kind == kind)
        else:
            # brand fonts are MediaItems too (contract v7) but don't belong in the picture grid
            stmt = stmt.where(MediaItem.kind.in_(("image", "video")))
        if origin:
            stmt = stmt.where(MediaItem.origin == origin)
        if home == "any":
            stmt = stmt.where(MediaItem.home.is_not(None))
        elif home:
            stmt = stmt.where(MediaItem.home == home)
        elif include_home:
            stmt = stmt.where(or_(MediaItem.home.is_(None), MediaItem.home.in_(include_home)))
        else:
            stmt = stmt.where(MediaItem.home.is_(None))
        if project_id:
            stmt = stmt.where(MediaItem.project_id == project_id)
        if scope is not None and scope.folder_id:
            stmt = stmt.where(MediaItem.folder_id == scope.folder_id)
        if scope is not None and scope.media_ids is not None:
            stmt = stmt.where(MediaItem.id.in_(scope.media_ids or {""}))
        if needle:
            like = f"%{needle}%"
            stmt = stmt.where(or_(func.lower(MediaItem.title).like(like),
                                  func.lower(cast(MediaItem.tags, String)).like(like)))
        if tag:
            # tags are stored lower-case and quote-free, so the JSON text match is exact enough
            t = (clean_tags([tag]) or [tag])[0]
            stmt = stmt.where(cast(MediaItem.tags, String).like(f'%"{t}"%'))
        if not (tag or "").lower().startswith(HIDDEN_TAGS):
            # dialogue line takes (contract v17) and a shot's spoken lines (v26) are working files of their
            # script or shot; asking for the owner's own tag is the one way to list them
            for hidden in HIDDEN_TAGS:
                stmt = stmt.where(~cast(MediaItem.tags, String).like(f'%"{hidden}%'))
        if after:
            stmt = stmt.where(or_(MediaItem.created_at < after[0],
                                  (MediaItem.created_at == after[0]) & (MediaItem.id < after[1])))
        stmt = stmt.order_by(MediaItem.created_at.desc(), MediaItem.id.desc()).limit(limit + 1)
        merged += [(i.created_at, i.id, i) for i in db.scalars(stmt)]

    only = sorted(scope.gen_ids) if scope is not None and scope.gen_ids is not None else None
    if (origin == "project" or (origin is None and include_project)) and not tag and only != [] and not home:
        for g, row in _project_rows(db, workspace_id, kind=kind, project_id=project_id, only_ids=only, want=want):
            if needle and needle not in f"{row.title} {row.project_title or ''}".lower():
                continue
            if after and (row.created_at, row.id) >= after:
                continue
            merged.append((row.created_at, row.id, row))

    merged.sort(key=lambda r: (r[0], r[1]), reverse=True)
    page, more = merged[:limit], len(merged) > limit
    standalone = items_out(db, [r[2] for r in page if isinstance(r[2], MediaItem)])
    by_id = {o.id: o for o in standalone}
    out = [by_id[r[1]] if isinstance(r[2], MediaItem) else r[2] for r in page]
    return out, (_cursor(page[-1][0], page[-1][1]) if more and page else None)


def project_row(db: Session, workspace_id: str, gen_id: str) -> tuple[Generation, MediaItemOut] | None:
    rows = _project_rows(db, workspace_id, kind=None, project_id=None, only_ids=[gen_id])
    return rows[0] if rows else None


def detail(db: Session, workspace_id: str, id_: str) -> MediaDetailOut | None:
    item = db.get(MediaItem, id_)
    if item is not None and item.removed_at is not None:
        return None
    if item is not None and item.workspace_id == workspace_id:
        versions = db.scalars(select(Generation).where(
            Generation.target_type == "media", Generation.target_id == item.id,
        ).order_by(Generation.version.desc(), Generation.created_at.desc())).all()
        return MediaDetailOut(**item_out(db, item).model_dump(), versions=[gen_out(g) for g in versions])
    found = project_row(db, workspace_id, id_)
    if found is None:
        return None
    g, row = found
    if g.kind == "render":
        title = (g.params or {}).get("title") or "Full film"
        siblings = [x for x in db.scalars(select(Generation).where(
            Generation.project_id == g.project_id, Generation.target_type == "project", Generation.kind == "render",
            Generation.status.in_(FINISHED)).order_by(Generation.created_at.desc()))
            if ((x.params or {}).get("title") or "Full film") == title]
    else:
        siblings = db.scalars(select(Generation).where(
            Generation.target_type == g.target_type, Generation.target_id == g.target_id, Generation.kind == g.kind,
            Generation.status != "rejected").order_by(Generation.version.desc())).all()
    return MediaDetailOut(**row.model_dump(), versions=[gen_out(x) for x in siblings])


# ---------------------------------------------------------------- lifecycle

def touch(item: MediaItem) -> None:
    item.updated_at = utcnow()


def delete_item(db: Session, item: MediaItem) -> None:
    gens = db.scalars(select(Generation).where(Generation.target_type == "media",
                                               Generation.target_id == item.id)).all()
    ids = [g.id for g in gens]
    if ids:
        # a worker mid-job sees "cancelled" on its next progress tick and stops
        db.execute(update(Job).where(Job.generation_id.in_(ids), Job.status.in_(("queued", "running")))
                   .values(status="cancelled", message="Cancelled: item deleted", finished_at=utcnow(),
                           updated_at=utcnow()))
    root = get_settings().data_dir
    own = [g.file_path for g in gens if g.file_path]
    # a RAW import's 16-bit master sits beside its working copy
    own += [(Path(f).parent / (Path(f).stem + ".master.tif")).as_posix() for f in own]
    files = own + [t.as_posix() for f in own for t in thumbs.files_for(Path(f))] +         ([item.thumb_path] if item.thumb_path else [])
    for g in gens:
        db.delete(g)
    db.delete(item)
    db.flush()
    for rel in files:
        try:
            (root / rel).unlink(missing_ok=True)
        except OSError:
            log.warning("couldn't delete %s", rel)


def _dims_of(g: Generation) -> tuple[int | None, int | None, float | None]:
    p = g.params or {}
    if (g.media_type or "").startswith("model/"):
        return None, None, None
    if (g.media_type or "").startswith("audio/"):
        from app import reel as rl

        return None, None, round(rl.probe(get_settings().data_dir / g.file_path).duration, 3) or p.get("duration_s")
    if (g.media_type or "").startswith("video/"):
        size = p.get("size") if isinstance(p.get("size"), list) else None
        if size:
            return int(size[0]), int(size[1]), p.get("duration_s")
        from app import reel as rl

        pr = rl.probe(get_settings().data_dir / g.file_path)
        return pr.width or None, pr.height or None, round(pr.duration, 3) or None
    w, h = _image_dims(g)
    return w, h, None


def on_job_finished(db: Session, job: Job) -> None:
    """Worker hook: a media generation finished (or failed), so the item's current version may move."""
    g = db.get(Generation, job.generation_id) if job.generation_id else None
    if g is None or g.target_type != "media":
        return
    item = db.get(MediaItem, g.target_id)
    if item is None:
        return
    cur = db.get(Generation, item.generation_id) if item.generation_id else None
    if g.status in FINISHED and g.file_path and (
            cur is None or cur.id == g.id or cur.status not in FINISHED or g.version > cur.version):
        item.generation_id = g.id
        try:
            item.width, item.height, dur = _dims_of(g)
            if dur is not None or item.kind == "image":
                item.duration_s = dur
        except Exception:
            log.exception("couldn't read the size of %s", g.id)
    touch(item)


# ---------------------------------------------------------------- upscales (P5)

IMAGE_UPSCALE_KINDS = ("portrait", "sheet_view", "establishing", "keyframe_start", "keyframe_mid", "keyframe_end",
                       "image")
VIDEO_UPSCALE_KINDS = ("render", "video")
UPSCALE_SCAN = 200


def _upscale_title(g: Generation, items: dict[str, MediaItem], names: dict[str, str]) -> str:
    if g.target_type == "media":
        item = items.get(g.target_id)
        return (item.title if item else "") or ("Video" if g.kind == "video" else "Image")
    if g.kind == "render":
        return ((g.params or {}).get("title") or "Full film").split(" · ")[0]
    label = KIND_LABELS.get(g.kind, g.kind.replace("_", " ").title())
    owner = names.get(g.target_id)
    return f"{owner} · {label}" if owner else label


def list_upscales(db: Session, workspace_id: str, *, kind: str | None = None, limit: int = 20,
                  cursor: str | None = None, want: bool | None = None) -> tuple[list[dict], str | None]:
    """Every upscale result in the workspace, newest first, running and failed ones included.

    Upscale results are the generations whose params carry "upscale"; they always have a parent, so the
    SQL narrows to children and the JSON check happens here (portable across SQLite and Postgres)."""
    limit = max(1, min(MAX_LIMIT, limit))
    after = parse_cursor(cursor)
    kinds = IMAGE_UPSCALE_KINDS if kind == "image" else VIDEO_UPSCALE_KINDS if kind == "video" else \
        IMAGE_UPSCALE_KINDS + VIDEO_UPSCALE_KINDS
    found: list[Generation] = []
    while len(found) <= limit:
        q = select(Generation).where(Generation.workspace_id == workspace_id, Generation.parent_id.is_not(None),
                                     Generation.kind.in_(kinds), Generation.status != "rejected",
                                     *vis.where(Generation, want))
        if after:
            q = q.where(or_(Generation.created_at < after[0],
                            (Generation.created_at == after[0]) & (Generation.id < after[1])))
        batch = db.scalars(q.order_by(Generation.created_at.desc(), Generation.id.desc()).limit(UPSCALE_SCAN)).all()
        found += [g for g in batch if isinstance((g.params or {}).get("upscale"), dict)]
        if len(batch) < UPSCALE_SCAN:
            break
        after = (batch[-1].created_at, batch[-1].id)
    page, more = found[:limit], len(found) > limit
    if not page:
        return [], None

    sources = {g.id: g for g in db.scalars(select(Generation).where(Generation.id.in_({g.parent_id for g in page})))}
    media_ids = {g.target_id for g in page if g.target_type == "media"}
    items = {i.id: i for i in db.scalars(select(MediaItem).where(MediaItem.id.in_(media_ids)))} if media_ids else {}
    job_ids = [g.job_id for g in page if g.job_id]
    jobs = {j.id: j for j in db.scalars(select(Job).where(Job.id.in_(job_ids)))} if job_ids else {}
    names: dict[str, str] = {}
    chars = [g.target_id for g in page if g.target_type == "character"]
    locs = [g.target_id for g in page if g.target_type == "location"]
    if chars:
        names.update(db.execute(select(Character.id, Character.name).where(Character.id.in_(chars))).all())
    if locs:
        names.update(db.execute(select(Location.id, Location.name).where(Location.id.in_(locs))).all())

    from app.services import job_out

    rows = []
    for g in page:
        up = g.params["upscale"]
        job = jobs.get(g.job_id) if g.job_id else None
        src = sources.get(g.parent_id)
        rows.append({
            "result": gen_out(g), "source": gen_out(src) if src is not None and src.workspace_id == workspace_id else None,
            "title": _upscale_title(g, items, names),
            "kind": "video" if g.kind in VIDEO_UPSCALE_KINDS else "image",
            "media_id": g.target_id if g.target_type == "media" and g.target_id in items else None,
            "project_id": g.project_id, "engine": up.get("engine"), "target": up.get("target"),
            "label": UPSCALE_LABELS.get(str(up.get("target")), str(up.get("target") or "").upper() or None),
            "width": up.get("width"), "height": up.get("height"), "status": g.status,
            "job": job_out(job) if job is not None else None,
            "error": (job.error if job is not None and job.status == "failed" else None)
                     or (g.note if g.status == "failed" else None),
        })
    return rows, (_cursor(page[-1].created_at, page[-1].id) if more else None)


def delete_upscale(db: Session, g: Generation) -> None:
    """Drop one upscaled version; the version it was made from stays and becomes current again."""
    if g.status in ("queued", "generating"):
        db.execute(update(Job).where(Job.generation_id == g.id, Job.status.in_(("queued", "running")))
                   .values(status="cancelled", message="Cancelled: upscale deleted", finished_at=utcnow(),
                           updated_at=utcnow()))
    if g.target_type == "media":
        item = db.get(MediaItem, g.target_id)
        if item is not None and item.generation_id == g.id:
            parent = db.get(Generation, g.parent_id) if g.parent_id else None
            if parent is None or parent.status not in FINISHED:
                parent = db.scalars(select(Generation).where(
                    Generation.target_type == "media", Generation.target_id == item.id, Generation.id != g.id,
                    Generation.status.in_(FINISHED)).order_by(Generation.version.desc())).first()
            if parent is not None:
                item.generation_id = parent.id
                try:
                    item.width, item.height, dur = _dims_of(parent)
                    if dur is not None:
                        item.duration_s = dur
                except Exception:
                    log.exception("couldn't read the size of %s", parent.id)
            touch(item)
    # an upscale of this upscale keeps its file and now hangs off the original
    db.execute(update(Generation).where(Generation.parent_id == g.id).values(parent_id=g.parent_id))
    rel = g.file_path
    db.delete(g)
    db.flush()
    if rel:
        src = get_settings().data_dir / rel
        src.unlink(missing_ok=True)
        thumbs.remove_for(src)
