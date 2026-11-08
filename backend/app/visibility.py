"""Who made a row, and whether the studio app lists it (contract v34).

Rows created through the token REST API carry via_api=True and stay out of the app's lists; the API (and
the MCP server) still see everything in their workspace. models._stamp_via_api does the stamping: a token
request flags its session in security.authenticate, and the worker flags its session from the job it runs,
so results, child jobs and Library items of an API job are API rows too.

mark() is the after-the-fact version for rows made before the column existed (POST /api/admin/mark-api and
scripts/mark-api-items.py).
"""
from datetime import datetime

from sqlalchemy import String, cast, select, update
from sqlalchemy.orm import Session

from app.models import (
    AdCampaign, Album, BrandKit, Character, DubProject, Folder, Generation, Job, Look, MediaItem, PodcastEpisode,
    PodcastShow, PoetryShort, Project, Transcript, UserTemplate, VideoPodcast, VoiceProfile, VoiceScript,
)
from app.security import CurrentUser, is_admin


def wanted(cur: CurrentUser) -> bool | None:
    """Which via_api value a listing keeps: False for the app, True for an admin's "only the API" view, None
    for everything (an API or MCP caller, or an admin asking for all)."""
    if cur.channel != "app":
        return None
    if cur.via in ("api", "all") and is_admin(cur):
        return True if cur.via == "api" else None
    return False


def where(model, want: bool | None) -> list:
    out = [] if want is None else [model.via_api == want]
    # removed by a moderator (contract v42): out of every list, whichever channel asks
    if hasattr(model, "removed_at"):
        out.append(model.removed_at.is_(None))
    return out


def keep(row, want: bool | None) -> bool:
    if getattr(row, "removed_at", None) is not None:
        return False
    return want is None or bool(getattr(row, "via_api", False)) == want


# ---------------------------------------------------------------- marking rows after the fact

KINDS = {
    "media": MediaItem, "generation": Generation, "job": Job, "project": Project, "character": Character,
    "voice": VoiceProfile, "script": VoiceScript, "dub": DubProject, "transcript": Transcript,
    "podcast_show": PodcastShow, "podcast_episode": PodcastEpisode, "video_podcast": VideoPodcast,
    "poetry": PoetryShort, "brand_kit": BrandKit, "template": UserTemplate, "folder": Folder, "album": Album,
    "look": Look, "ad_campaign": AdCampaign,
}
STORY_JOBS = ("story_audio", "story_video")
SAMPLE = 20


class MarkError(ValueError):
    pass


def story_refs(job_type: str, payload: dict) -> tuple[set[str], set[str]]:
    """(generation ids, file paths) a story job used: the music bed; the pages and title card's files."""
    p = payload or {}
    gens, files = set(), set()
    if job_type == "story_audio" and p.get("music_generation_id"):
        gens.add(p["music_generation_id"])
    if job_type == "story_video":
        files |= {f for f in (p.get("pictures") or {}).values() if isinstance(f, str)}
        tc = p.get("title_card") or {}
        if isinstance(tc, dict) and tc.get("file"):
            files.add(tc["file"])
    return gens, files


class _Found:
    def __init__(self):
        self.ids: dict[str, set[str]] = {k: set() for k in KINDS}

    def add(self, kind: str, ids) -> set[str]:
        new = {i for i in ids if i} - self.ids[kind]
        self.ids[kind] |= new
        return new


def _scoped(stmt, model, workspace_id: str | None):
    return stmt.where(model.workspace_id == workspace_id) if workspace_id else stmt


def _follow(db: Session, found: _Found, workspace_id: str | None) -> None:
    """Pull in what hangs off the rows found so far: a project's films and cast, an item's versions, a job's
    generation and children, a story job's pictures. Loops until nothing new turns up."""
    todo = {k: set(v) for k, v in found.ids.items()}
    for _ in range(8):
        nxt: dict[str, set[str]] = {k: set() for k in KINDS}
        if todo["project"]:
            pids = todo["project"]
            for kind, model in (("generation", Generation), ("job", Job), ("character", Character),
                                ("media", MediaItem), ("video_podcast", VideoPodcast)):
                nxt[kind] |= found.add(kind, db.scalars(select(model.id).where(model.project_id.in_(pids))))
        if todo["media"]:
            nxt["generation"] |= found.add("generation", db.scalars(select(Generation.id).where(
                Generation.target_type == "media", Generation.target_id.in_(todo["media"]))))
        if todo["generation"]:
            rows = db.execute(select(Generation.target_type, Generation.target_id, Generation.job_id)
                              .where(Generation.id.in_(todo["generation"]))).all()
            nxt["media"] |= found.add("media", [t_id for t_type, t_id, _ in rows if t_type == "media"])
            nxt["job"] |= found.add("job", [j for _, _, j in rows if j])
            nxt["job"] |= found.add("job", db.scalars(select(Job.id).where(Job.generation_id.in_(todo["generation"]))))
        for kind in ("dub", "transcript", "poetry", "video_podcast", "ad_campaign"):
            if todo[kind]:
                model = KINDS[kind]
                nxt["job"] |= found.add("job", db.scalars(select(model.job_id).where(model.id.in_(todo[kind]))))
        if todo["job"]:
            jobs = db.execute(select(Job.id, Job.type, Job.payload, Job.generation_id)
                              .where(Job.id.in_(todo["job"]))).all()
            nxt["generation"] |= found.add("generation", [g for *_, g in jobs if g])
            files: set[str] = set()
            for _, type_, payload, _ in jobs:
                if type_ in STORY_JOBS:
                    gens, paths = story_refs(type_, payload)
                    nxt["generation"] |= found.add("generation", gens)
                    files |= paths
            if files:
                nxt["generation"] |= found.add("generation", db.scalars(_scoped(
                    select(Generation.id).where(Generation.file_path.in_(files)), Generation, workspace_id)))
            for jid in todo["job"]:
                # children say who they belong to in their payload (autopilot, video podcast, Poetry Shorts)
                kids = db.execute(_scoped(select(Job.id, Job.payload).where(cast(Job.payload, String).like(f"%{jid}%")),
                                          Job, workspace_id)).all()
                nxt["job"] |= found.add("job", [k for k, p in kids
                                                if (p or {}).get("parent_job_id") == jid or (p or {}).get("inline_of") == jid])
        if not any(nxt.values()):
            return
        todo = nxt


def mark(db: Session, *, workspace_id: str | None = None, ids: list[str] | None = None,
         since: datetime | None = None, until: datetime | None = None, kinds: list[str] | None = None,
         apply: bool = False, follow: bool = True) -> dict:
    """Mark rows as made through the API. ids may be of any kind (and pull in what hangs off them); since /
    until (naive UTC) take every row of `kinds` created in that window. Nothing is written unless apply."""
    kinds = list(kinds or KINDS)
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise MarkError(f"Unknown kind '{bad[0]}' (expected {', '.join(KINDS)})")
    if not ids and since is None and until is None:
        raise MarkError("Give ids, or a since / until window")
    found = _Found()
    for kind in kinds:
        model = KINDS[kind]
        if ids:
            found.add(kind, db.scalars(_scoped(select(model.id).where(model.id.in_(ids)), model, workspace_id)))
        if since is not None or until is not None:
            q = _scoped(select(model.id), model, workspace_id)
            if since is not None:
                q = q.where(model.created_at >= since)
            if until is not None:
                q = q.where(model.created_at < until)
            found.add(kind, db.scalars(q))
    if follow:
        _follow(db, found, workspace_id)

    out: dict = {"apply": apply, "counts": {}, "already": {}, "sample": {}}
    for kind, got in found.ids.items():
        if not got:
            continue
        model = KINDS[kind]
        fresh = sorted(db.scalars(_scoped(select(model.id).where(model.id.in_(got), model.via_api == False),  # noqa: E712
                                          model, workspace_id)))
        out["counts"][kind] = len(fresh)
        out["already"][kind] = len(got) - len(fresh)
        out["sample"][kind] = fresh[:SAMPLE]
        if apply and fresh:
            # updated_at left alone: marking isn't an edit, and the SSE stream shouldn't replay old rows
            values = {"via_api": True}
            if hasattr(model, "updated_at"):
                values["updated_at"] = model.updated_at  # or the column's onupdate fires
            db.execute(update(model).where(model.id.in_(fresh)).values(**values)
                       .execution_options(synchronize_session=False))
    if ids:
        known = set().union(*found.ids.values())
        out["unknown_ids"] = [i for i in ids if i not in known]
    if apply:
        db.commit()
    return out
