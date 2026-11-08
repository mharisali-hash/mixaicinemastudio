"""ORM models. Every domain row carries workspace_id so multi-tenant later is a filter, not a rewrite."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, object_session

from app.db import Base
from app.lanes import lane_for


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    # stored naive-UTC; SQLite has no tz type and mixing aware/naive bites on compare
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _id():
    return mapped_column(String(36), primary_key=True, default=new_id)


def _ws():
    return mapped_column(String(36), ForeignKey("workspace.id", ondelete="CASCADE"), index=True)


def _via():
    # made through the token REST API (contract v34): kept out of the studio app's own lists
    return mapped_column(Boolean, default=False, server_default=text("0"), index=True)


class Workspace(Base):
    __tablename__ = "workspace"
    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(200))
    # no FK: adding one means rebuilding the table on SQLite (see Scene.location_id)
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "user"
    __table_args__ = (Index("ix_user_handle", "handle", unique=True),
                      Index("ix_user_google_sub", "google_sub", unique=True))
    id: Mapped[str] = _id()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    # "" for an account with no password yet (an invite that hasn't been taken up)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # slug for the public pages that come later
    handle: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # in the session cookie; bumping it signs out every browser at once
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # contract v39: the admin's per-user override of the package's limits; null keeps the package's value
    limit_gpu_min_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_gpu_min_month: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_max_queued: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # M22: Implant Preview is opt-in per account; only an admin switches it on (admins always have it)
    implant_access: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmailToken(Base):
    """A link sent by email (verify, reset, invite). Only the sha256 of the secret is kept; used once."""
    __tablename__ = "email_token"
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = _ws()
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    __tablename__ = "project"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    title: Mapped[str] = mapped_column(String(300))
    logline: Mapped[str] = mapped_column(Text, default="")
    brief: Mapped[str] = mapped_column(Text, default="")
    authoring_mode: Mapped[str] = mapped_column(String(20))
    aspect_ratio: Mapped[str] = mapped_column(String(10), default="16:9")
    target_runtime_s: Mapped[int] = mapped_column(Integer, default=120)
    quality: Mapped[str] = mapped_column(String(10), default="draft")
    takes_per_shot: Mapped[int] = mapped_column(Integer, default=3)
    overnight: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # look suffix every frame/motion prompt carries verbatim (world reconstruction)
    style_bible: Mapped[str] = mapped_column(Text, default="", server_default="")
    # free-form per-project defaults (migration 0006): brand_kit_id, image_model, ...
    settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class Scene(Base):
    __tablename__ = "scene"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True)
    # "order" is an SQL keyword; keep the attribute name the API uses
    order: Mapped[int] = mapped_column("sort_order", Integer, default=0)
    heading: Mapped[str] = mapped_column(String(300), default="")
    logline: Mapped[str] = mapped_column(Text, default="")
    script_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    time_of_day: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mood: Mapped[str] = mapped_column(String(200), default="")
    lighting: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(20), default="user")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    # no FK on purpose: adding one to an existing SQLite table means a table rebuild,
    # which with foreign_keys=ON cascades into scene_version. Cleared in code on delete.
    location_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def shots_review(self) -> str | None:
        # the shot-list review gate lives in project.settings["shots_review"][scene_id], so no migration
        s = object_session(self)
        p = s.get(Project, self.project_id) if s is not None and self.project_id else None
        return ((p.settings or {}).get("shots_review") or {}).get(self.id) if p is not None else None


class SceneVersion(Base):
    __tablename__ = "scene_version"
    __table_args__ = (UniqueConstraint("scene_id", "version"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    scene_id: Mapped[str] = mapped_column(String(36), ForeignKey("scene.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(300), default="")
    logline: Mapped[str] = mapped_column(Text, default="")
    script_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Character(Base):
    __tablename__ = "character"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    # NULL = a Character Studio character, shared into films through project_cast (migration 0018)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="user")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # role, age, height, origin, languages, personality, accent, wardrobe, details[] (app.character_sheet)
    profile: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # a library voice id or "gallery:<id>" (M12b)
    voice_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # sheet state: preset, panel extras, hero-sourced panels waiting, palette, board (app.character_sheet)
    sheet: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # how they move on camera: expressiveness calm | natural | lively, and a gestures note (migration 0031)
    performance: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CharacterLook(Base):
    """One outfit / setting / pose of a character (HeyGen calls them looks). Its pictures are generations with
    target_type "character_look", so each look keeps its own approved picture (migration 0031)."""
    __tablename__ = "character_look"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    character_id: Mapped[str] = mapped_column(String(36), ForeignKey("character.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    outfit: Mapped[str] = mapped_column(Text, default="")
    setting: Mapped[str] = mapped_column(Text, default="")
    pose: Mapped[str] = mapped_column(Text, default="")
    # anything else to say about it, in the user's words
    prompt: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PresenterVideo(Base):
    """A character (in one of its looks) presenting a script to camera, scene by scene (migration 0032). Each
    scene keeps its own voice take, frame and lip-synced take, so editing one line redoes only that scene."""
    __tablename__ = "presenter_video"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    character_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("character.id", ondelete="SET NULL"),
                                                     nullable=True)
    look_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    language: Mapped[str] = mapped_column(String(12), default="en")
    # aspect, quality, caption_style, background, lower third, title card
    settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # [{id, text, background, speech: {fp, gen}, frame: {fp, gen}, take: {draft|final: {fp, gen}}, duration_s}]
    scenes: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    stages: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # {draft|final: {media_id, generation_id, duration_s, ...}}
    outputs: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProjectCast(Base):
    """A studio character cast in a film. The film's own characters don't need a row."""

    __tablename__ = "project_cast"
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True)
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("character.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Location(Base):
    __tablename__ = "location"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="user")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    time_of_day_variants: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Shot(Base):
    __tablename__ = "shot"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True)
    scene_id: Mapped[str] = mapped_column(String(36), ForeignKey("scene.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column("sort_order", Integer, default=0)
    shot_type: Mapped[str] = mapped_column(String(30), default="medium")
    duration_s: Mapped[float] = mapped_column(Float, default=4.0)
    description: Mapped[str] = mapped_column(Text, default="")
    camera: Mapped[str] = mapped_column(String(300), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    prompt_mode: Mapped[str] = mapped_column(String(10), default="auto")
    start_prompt: Mapped[str] = mapped_column(Text, default="")
    end_prompt: Mapped[str] = mapped_column(Text, default="")
    motion_prompt: Mapped[str] = mapped_column(Text, default="")
    character_ids: Mapped[list] = mapped_column(JSON, default=list)
    location_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seam_in: Mapped[str] = mapped_column(String(10), default="cut")
    handoff_text: Mapped[str] = mapped_column(Text, default="")
    # long-take beat list (migration 0004); owned by the long-take engine
    beats: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # [{asset_id, asset_type: logo|product, surface, prominence: hero|background}] (migration 0006)
    brand_placements: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # {size, angle, motion, speed} from the camera rack (migration 0007); `camera` keeps the readable text
    camera_rack: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # {character_id: sheet panel key} the user picked over the automatic choice (migration 0018)
    ref_panels: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # [{character_id, text, pause_after_s?}] spoken lines (M12b, migration 0018)
    dialogue: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    stale:Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="user")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class Reel(Base):
    __tablename__ = "reel"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project.id", ondelete="CASCADE"), unique=True
    )
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class ReelClip(Base):
    __tablename__ = "reel_clip"
    __table_args__ = (UniqueConstraint("reel_id", "shot_id", name="uq_reel_clip_shot"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    reel_id: Mapped[str] = mapped_column(String(36), ForeignKey("reel.id", ondelete="CASCADE"), index=True)
    shot_id: Mapped[str] = mapped_column(String(36), ForeignKey("shot.id", ondelete="CASCADE"))
    scene_id: Mapped[str] = mapped_column(String(36), ForeignKey("scene.id", ondelete="CASCADE"), index=True)
    # not an FK: takes are bulk-deleted with their shot; sync handles a dangling id
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    order: Mapped[int] = mapped_column("sort_order", Integer, default=0)
    trim_in_s: Mapped[float] = mapped_column(Float, default=0.0)
    trim_out_s: Mapped[float] = mapped_column(Float, default=0.0)
    transition_in: Mapped[str] = mapped_column(String(12), default="cut")
    transition_s: Mapped[float] = mapped_column(Float, default=0.5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class Generation(Base):
    __tablename__ = "generation"
    __table_args__ = (
        Index("ix_generation_target", "target_type", "target_id", "kind"),
        # backstop for the "one approved per target+kind" rule enforced in the API. Sheet views are
        # one approved per panel (params.panel), which an index can't see, so they're left out (0018).
        Index(
            "uq_generation_one_approved",
            "target_type", "target_id", "kind",
            unique=True,
            sqlite_where=text("status = 'approved' AND kind != 'sheet_view'"),
            postgresql_where=text("status = 'approved' AND kind != 'sheet_view'"),
        ),
    )
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True, nullable=True
    )
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    prompt: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    score: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generation.id", ondelete="SET NULL"), nullable=True
    )
    # not an FK: job.generation_id already points this way and SQLite dislikes cycles
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status_created", "status", "created_at"),
                      # the fair queue's "who ran last in this lane" lookup (worker.claim_next)
                      Index("ix_job_user_lane_started", "user_id", "lane", "started_at"))
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    # who asked for it; child jobs carry their root's user (_stamp_user)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(500), default="")
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True, nullable=True
    )
    generation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("generation.id", ondelete="SET NULL"), nullable=True
    )
    gpu: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # image | video | general (app.lanes); set on insert by _classify_new_jobs, read by lane workers
    lane: Mapped[str] = mapped_column(String(10), default="general", server_default="general", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    gpu_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class UsageLedger(Base):
    __tablename__ = "usage_ledger"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("job.id", ondelete="SET NULL"), nullable=True)
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    info: Mapped[dict] = mapped_column(JSON, default=dict)


class Suggestion(Base):
    """An AI edit the user hasn't accepted yet (PLAN 2b: suggestions, not silent edits)."""

    __tablename__ = "suggestion"
    __table_args__ = (Index("ix_suggestion_target", "target_type", "target_id", "status"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    field: Mapped[str] = mapped_column(String(40))
    current_text: Mapped[str] = mapped_column(Text, default="")
    proposed_text: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("job.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MediaItem(Base):
    """A standalone image or video (Image studio result, upload, standalone upscale). Contract v5.

    Its versions are generations with target_type="media", target_id=this id; generation_id is the
    current one. Project results show up in the Library as computed rows, never as MediaItems."""

    __tablename__ = "media_item"
    __table_args__ = (Index("ix_media_item_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    kind: Mapped[str] = mapped_column(String(10))  # image | video | audio (and brand fonts)
    origin: Mapped[str] = mapped_column(String(20))  # generated | upload
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    tags: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # not an FK, same reason as generation.job_id
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # migration 0008; not an FK (ADD COLUMN on SQLite), cleared in code when a folder goes
    folder_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # photo catalogue (migration 0011): Lightroom-style marks and what the file says about itself
    rating: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), index=True)
    flag: Mapped[str] = mapped_column(String(8), default="", server_default="", index=True)  # pick | reject | ""
    label: Mapped[str] = mapped_column(String(10), default="", server_default="", index=True)
    caption: Mapped[str] = mapped_column(Text, default="", server_default="")
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    camera: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lens: Mapped[str | None] = mapped_column(String(160), nullable=True)
    focal_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    aperture: Mapped[float | None] = mapped_column(Float, nullable=True)
    shutter_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    iso: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    exif: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    original_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # RAW / HEIC / TIFF uploads: the file as it came; generation_id points at the sRGB working copy
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # migration 0026: the studio this is a working part of; None is a final output, listed everywhere
    home: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class Client(Base):
    """A photographer's client (M10). Shoots are albums of kind "shoot" pointing here."""

    __tablename__ = "client"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(200), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(60), default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Album(Base):
    """Lightroom collections. kind: folder (holds albums, nests) | album | smart (rules) | shoot (an album
    with client, date and venue). Unlike Folder, a photo can be in any number of albums."""

    __tablename__ = "album"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="album", server_default="album")
    name: Mapped[str] = mapped_column(String(160))
    client_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("client.id", ondelete="SET NULL"), nullable=True, index=True)
    shoot_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    venue: Mapped[str] = mapped_column(String(200), default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cover_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AlbumItem(Base):
    __tablename__ = "album_item"
    __table_args__ = (UniqueConstraint("album_id", "media_id", name="uq_album_item"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    album_id: Mapped[str] = mapped_column(String(36), ForeignKey("album.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[str] = mapped_column(String(36), ForeignKey("media_item.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PhotoSnapshot(Base):
    """A named develop state for one picture (Lightroom Snapshots). Settings only; nothing is rendered."""

    __tablename__ = "photo_snapshot"
    __table_args__ = (Index("ix_photo_snapshot_target", "target_type", "target_id"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    base_id: Mapped[str] = mapped_column(String(36))  # the version the params develop
    name: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Folder(Base):
    """A Library folder (polish P4). Nested through parent_id; deleting one hands its contents to the parent."""

    __tablename__ = "folder"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="any", server_default="any")  # any | image | video | audio
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FolderLink(Base):
    """Files a project result (read-through row, ref "gen:<generation id>") into a folder without copying it."""

    __tablename__ = "folder_link"
    __table_args__ = (UniqueConstraint("workspace_id", "media_ref", name="uq_folder_link_ref"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    folder_id: Mapped[str] = mapped_column(String(36), ForeignKey("folder.id", ondelete="CASCADE"), index=True)
    media_ref: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Favourite(Base):
    __tablename__ = "favourite"
    __table_args__ = (UniqueConstraint("user_id", "media_ref", name="uq_favourite_ref"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    media_ref: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SavedFilter(Base):
    __tablename__ = "saved_filter"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    query: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BrandKit(Base):
    """Logo, products, palette, fonts and tone for a brand (contract v7). Asset fields hold MediaItem ids."""

    __tablename__ = "brand_kit"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    name: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    palette: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    style_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    voice_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    tagline: Mapped[str] = mapped_column(String(300), default="", server_default="")
    # {"primary": {media_id, description}, "light": ..., "dark": ...}
    logos: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    products: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    font_files: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    reference_media_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # contract v36: {phone, whatsapp, email, website, booking_url, location, opening_hours} for an advert's footer
    contact: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # [{platform, handle, url, show_in_ads}], at most 10 (migration 0024 moved the old contact.social here)
    socials: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # approved offer lines ("From 215 kr/hour", "No lock-in"); the ad copywriter may only use these
    claims: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # M16b: {references: [media ids], rules, status, error, job_id, updated_at} read from the brand's own adverts
    ad_style: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)


class Look(Base):
    """A Photo Studio look (contract v9): develop params, an imported .cube, or both.

    Built-ins have no workspace (shared, read-only) and fixed ids; app.photo.looks keeps them in step."""

    __tablename__ = "look"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspace.id", ondelete="CASCADE"), index=True, nullable=True
    )
    via_api: Mapped[bool] = _via()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    category: Mapped[str] = mapped_column(String(80), default="", server_default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    cube_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cube_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="user")  # builtin | user | imported
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VoiceProfile(Base):
    """A voice in the Voice studio library (contract v16): a cloned reference clip, a designed recipe
    (description + seed) or one of an engine's preset voices."""

    __tablename__ = "voice_profile"
    __table_args__ = (Index("ix_voice_profile_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), index=True)  # clone | design | preset
    language: Mapped[str] = mapped_column(String(12), default="en", server_default="en", index=True)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    gender_hint: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # the Library item and the version of it the voice was made from; pinned so a later edit of the
    # item doesn't quietly change the voice
    ref_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ref_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ref_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    ref_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    design: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {description, seed, engine}
    preset: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {engine, id}
    portrait_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    starred: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), index=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    consent: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {name, statement, at, user_id}
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VoiceScript(Base):
    """A dialogue / narration project (contract v17). Cast and lines are JSON: the editor saves the
    whole array at once and nothing queries into single lines."""

    __tablename__ = "voice_script"
    __table_args__ = (Index("ix_voice_script_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(12), default="en", server_default="en")
    engine: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cast: Mapped[list] = mapped_column(JSON, default=list)
    lines: Mapped[list] = mapped_column(JSON, default=list)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    mix: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DubProject(Base):
    """Dub a video (contract v18). The pipeline state, speakers and segments are JSON written whole by
    the stage jobs and the editor; the files (stems, takes, fitted lines) live in the dub's data dir."""

    __tablename__ = "dub_project"
    __table_args__ = (Index("ix_dub_project_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    video_id: Mapped[str] = mapped_column(String(36), index=True)
    # the version being dubbed, pinned so a later edit of the video doesn't shift every timing
    video_generation_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    source_language: Mapped[str | None] = mapped_column(String(12), nullable=True)
    targets: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(20), default="extract", server_default="extract")
    stages: Mapped[dict] = mapped_column(JSON, default=dict)
    speakers: Mapped[list] = mapped_column(JSON, default=list)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    glossary: Mapped[list] = mapped_column(JSON, default=list)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    tracks: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Transcript(Base):
    """A timed transcript of a Library audio or video item (contract v18)."""

    __tablename__ = "transcript"
    __table_args__ = (Index("ix_transcript_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    media_id: Mapped[str] = mapped_column(String(36), index=True)
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="", server_default="")
    language: Mapped[str | None] = mapped_column(String(12), nullable=True)
    words: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    segments: Mapped[list] = mapped_column(JSON, default=list)
    cleanup_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PodcastShow(Base):
    """A podcast (contract v20): defaults for its episodes and the ID3 fields they publish with."""

    __tablename__ = "podcast_show"
    __table_args__ = (Index("ix_podcast_show_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    author: Mapped[str] = mapped_column(String(200), default="", server_default="")
    language: Mapped[str] = mapped_column(String(12), default="en", server_default="en")
    cover_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    hosts: Mapped[list] = mapped_column(JSON, default=list)
    music: Mapped[dict] = mapped_column(JSON, default=dict)
    ads: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PodcastEpisode(Base):
    """One episode (contract v20). Its lines live in a VoiceScript, so renders and takes are Dialogue's;
    sources, hosts, chapters, produce settings and outputs are JSON written whole."""

    __tablename__ = "podcast_episode"
    __table_args__ = (Index("ix_podcast_episode_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    show_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("podcast_show.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(20), default="conversation", server_default="conversation")
    language: Mapped[str] = mapped_column(String(12), default="en", server_default="en")
    target_minutes: Mapped[float] = mapped_column(Float, default=5.0, server_default="5")
    step: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    cover_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    hosts: Mapped[list] = mapped_column(JSON, default=list)
    chapters: Mapped[list] = mapped_column(JSON, default=list)
    script_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voice_script.id", ondelete="SET NULL"), nullable=True
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VideoPodcast(Base):
    """An episode as an AI video (contract v24). Its characters, set, angle frames and takes belong to a
    hidden project (authoring_mode "podcast"), so the ordinary generation and approval flows apply."""

    __tablename__ = "video_podcast"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    episode_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("podcast_episode.id", ondelete="CASCADE"), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("project.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20), default="cast", server_default="cast")
    stages: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    hosts: Mapped[dict] = mapped_column(JSON, default=dict)  # {host_id: {character_id, source, ...}}
    set_location_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan: Mapped[list] = mapped_column(JSON, default=list)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ApiToken(Base):
    """Personal access tokens (contract v27). Only the sha256 of the secret is kept; the prefix finds the row."""
    __tablename__ = "api_token"
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = _ws()
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserTemplate(Base):
    """A prompt template someone saved in this workspace (contract v31). Shared by the workspace, like the Library;
    [slots] in the prompt work as in the shipped templates."""
    __tablename__ = "user_template"
    __table_args__ = (Index("ix_user_template_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # kept when the user goes
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    target: Mapped[str] = mapped_column(String(16), default="generate")  # generate | edit
    prompt: Mapped[str] = mapped_column(Text)
    negative: Mapped[str] = mapped_column(Text, default="", server_default="")
    style: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    aspect: Mapped[str | None] = mapped_column(String(8), nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    ref_labels: Mapped[list] = mapped_column(JSON, default=list)
    examples: Mapped[dict] = mapped_column(JSON, default=dict)
    # the result it was saved from, shown as the card's picture while it exists
    preview_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PoetryShort(Base):
    """A sung poem as a short video (contract v33). The poem's lines carry their timings; the scenes carry
    their keyframe and clip generation ids. All JSON, written whole by the stage job and the editor."""
    __tablename__ = "poetry_short"
    __table_args__ = (Index("ix_poetry_short_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    language: Mapped[str] = mapped_column(String(12), default="ur", server_default="ur")
    stage: Mapped[str] = mapped_column(String(20), default="poem", server_default="poem")
    stages: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    poem: Mapped[dict] = mapped_column(JSON, default=dict)  # {lyrics, tags, written}
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{text, section, start, end}]
    song: Mapped[dict] = mapped_column(JSON, default=dict)
    timing: Mapped[dict] = mapped_column(JSON, default=dict)
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdCampaign(Base):
    """An Ad Studio campaign (contract v36): the brief, the concepts with their photos, and one ad per concept
    and format with its editable layer spec. JSON, written whole by the stage job and the editor."""
    __tablename__ = "ad_campaign"
    __table_args__ = (Index("ix_ad_campaign_ws_updated", "workspace_id", "updated_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    via_api: Mapped[bool] = _via()
    # migration 0029: "ad", or "print" for a Print Studio job (contract v41)
    kind: Mapped[str] = mapped_column(String(20), default="ad", server_default="ad")
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    brand_kit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    language: Mapped[str] = mapped_column(String(12), default="en", server_default="en")
    stage: Mapped[str] = mapped_column(String(20), default="concepts", server_default="concepts")
    stages: Mapped[dict] = mapped_column(JSON, default=dict)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    concepts: Mapped[list] = mapped_column(JSON, default=list)
    ads: Mapped[list] = mapped_column(JSON, default=list)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Package(Base):
    """What a user may do (contract v39): GPU minutes, queue depth, models and studios. Null limit = no limit."""
    __tablename__ = "package"
    id: Mapped[str] = _id()
    # free / demo / pro for the seeded ones; "free" is everyone's fallback
    slug: Mapped[str | None] = mapped_column(String(60), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    price_pkr: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    period: Mapped[str] = mapped_column(String(10), default="month", server_default="month")
    # a demo's length, or how long an assignment lasts when the period is "none"
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpu_min_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpu_min_month: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_queued: Mapped[int | None] = mapped_column(Integer, nullable=True)
    private_items: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    # null = every model
    models: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {feature: false} switches a studio off; anything not named stays on
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    kind: Mapped[str] = mapped_column(String(10), default="standard", server_default="standard")
    public: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    custom_for_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Subscription(Base):
    __tablename__ = "subscription"
    __table_args__ = (Index("ix_subscription_user_status", "user_id", "status"),)
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"))
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("package.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # active | expired | cancelled | pending_payment
    status: Mapped[str] = mapped_column(String(20), default="active")
    # admin | demo | payment | signup_default
    source: Mapped[str] = mapped_column(String(20), default="admin")
    notes: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UsageGrant(Base):
    """Extra GPU minutes a day while it lasts, on top of the package."""
    __tablename__ = "usage_grant"
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    gpu_min: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(500), default="", server_default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Payment(Base):
    """A payment row (contract v39). The gateway connection comes in M19; for now the admin records and marks them."""
    __tablename__ = "payment"
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    package_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gateway: Mapped[str] = mapped_column(String(40), default="manual", server_default="manual")
    amount_pkr: Mapped[int] = mapped_column(Integer, default=0)
    # pending | paid | failed | refunded
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    reference: Mapped[str] = mapped_column(String(200), default="", server_default="")
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    id: Mapped[str] = _id()
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # kept as text so the row still reads right after the admin's account goes
    actor_email: Mapped[str] = mapped_column(String(320), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(20), default="")
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class BulkBatch(Base):
    """An admin's bulk run (migration 0033): thousands of uploaded pictures, one action for all of them.
    Its pictures, versions and jobs live in the hidden bulk workspace (app.bulk.service.workspace), so none
    of it reaches anyone's Library, job tray or event stream."""
    __tablename__ = "bulk_batch"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(20))  # cutout | edit
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    # uploading | running | paused | done | cancelled
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    run_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exports: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BulkItem(Base):
    __tablename__ = "bulk_item"
    __table_args__ = (UniqueConstraint("batch_id", "filename", "size", name="uq_bulk_item_file"),
                      Index("ix_bulk_item_batch_status", "batch_id", "status"))
    id: Mapped[str] = _id()
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("bulk_batch.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # as the admin's folder had it, sub-folders included ("shoes/red/001.jpg"); the export keeps the name
    filename: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_gen_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # from a prompts CSV; None means the batch's own instruction / keywords
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_gen_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # uploaded | queued | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ImplantPatient(Base):
    """M22: one surgeon's patient. Photos and results are Generation rows (implant_photo / implant_result) whose
    files live under data/clinic/, outside every workspace folder, so nothing else in the app can list or serve
    them."""
    __tablename__ = "implant_patient"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws()
    surgeon_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    adult_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # {at, attested_by, form_path, topless_ok}; nothing is generated until it's recorded
    consent: Mapped[dict] = mapped_column(JSON, default=dict)
    measurements: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    retention_months: Mapped[int] = mapped_column(Integer, default=24)
    # the approved option and its chart: {session_id, option, chart_path, approved_at, versions: [...]}
    chart: Mapped[dict] = mapped_column(JSON, default=dict)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ImplantSession(Base):
    """One round of options (up to three) rendered on the patient's photos."""
    __tablename__ = "implant_session"
    id: Mapped[str] = _id()
    patient_id: Mapped[str] = mapped_column(String(36), ForeignKey("implant_patient.id", ondelete="CASCADE"),
                                            index=True)
    options: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


VIA_API_MODELS = (Project, Character, Generation, Job, MediaItem, Folder, Album, BrandKit, Look, VoiceProfile,
                  VoiceScript, DubProject, Transcript, PodcastShow, PodcastEpisode, VideoPodcast, UserTemplate,
                  PoetryShort, AdCampaign)
# Session.info key: set for a token REST API request, and by the worker while it runs one of its jobs
VIA_API = "via_api"
# Session.info key: the user a request acts as (security.bind_session), or the user of the job the worker runs
ACTING_USER = "acting_user"


@event.listens_for(Session, "before_flush")
def _stamp_via_api(session, _ctx, _instances):
    if not session.info.get(VIA_API):
        return
    for obj in session.new:
        if isinstance(obj, VIA_API_MODELS):
            obj.via_api = True


@event.listens_for(Session, "before_flush")
def _stamp_user(session, _ctx, _instances):
    uid = session.info.get(ACTING_USER)
    if not uid:
        return
    for obj in session.new:
        if isinstance(obj, Job) and obj.user_id is None:
            obj.user_id = uid


@event.listens_for(Session, "before_flush")
def _classify_new_jobs(session, _ctx, _instances):
    # one place instead of every enqueue site (generate, upscale, brand, zip, autopilot, ai_*...)
    for obj in session.new:
        if not isinstance(obj, Job) or obj.lane:
            continue
        kind = None
        if obj.type == "generate" and obj.generation_id:
            gen = next((g for g in session.new if isinstance(g, Generation) and g.id == obj.generation_id), None)
            if gen is None:
                with session.no_autoflush:
                    gen = session.get(Generation, obj.generation_id)
            kind = gen.kind if gen is not None else None
        obj.lane = lane_for(obj.type, kind)
