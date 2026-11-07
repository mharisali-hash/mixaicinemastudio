"""Request/response shapes. Kept in one file while the surface is small."""
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from app.limits import PROMPT_MAX
from app.camera import Camera


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


Utc = Annotated[datetime, PlainSerializer(_iso, return_type=str)]

AuthoringMode = Literal["ai_director", "scene_by_scene", "import"]
Quality = Literal["draft", "final"]
ProjectStatus = Literal["draft", "in_progress", "rendering", "done"]
TimeOfDay = Literal["dawn", "morning", "day", "golden_hour", "dusk", "night", "interior"]
TargetType = Literal["character", "scene", "shot", "location", "project"]
GenKind = Literal[
    "portrait", "sheet_view", "keyframe_start", "keyframe_end", "keyframe_mid",
    "take", "tile", "render", "scene_text", "establishing",
]
ShotType = Literal[
    "wide", "medium", "close_up", "extreme_close_up", "over_shoulder", "pov", "insert", "establishing", "long_take",
]
Seam = Literal["cut", "continue"]
PromptMode = Literal["auto", "manual"]


def _take_length(v: float | None) -> float | None:
    from app.config import get_settings

    top = get_settings().longtake_max_s
    if v is not None and not 1 <= v <= top:
        raise ValueError(f"must be between 1 and {top:g} seconds")
    return v


# 1..LONGTAKE_MAX_S (a setting, so it can't be a static Field bound)
TakeSeconds = Annotated[float, AfterValidator(_take_length)]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# auth
class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(Out):
    id: str
    email: str
    display_name: str
    workspace_id: str
    role: str
    # contract v27: whether Settings shows API tokens, and whether /api/docs opens for this user
    api_access: bool = False
    api_docs: bool = False
    # contract v34: the admin may list what was made through the API (?via=api) in the Library and Queue
    admin: bool = False
    # contract v38: false until the email link is opened; such a user can look around but not queue work
    verified: bool = True
    # M22: Implant Preview in the rail (admins, and accounts an admin switched it on for)
    implant: bool = False


# accounts (contract v38)
class SignupIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    name: str = Field(default="", max_length=200)


class EmailIn(BaseModel):
    email: str = Field(default="", max_length=320)


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ProfilePatch(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class AdminUserOut(BaseModel):
    id: str
    email: str
    display_name: str
    is_admin: bool
    is_active: bool
    verified: bool
    workspace_id: str | None
    created_at: Utc
    last_login_at: Utc | None
    gpu_min_today: float
    gpu_min_month: float
    jobs: int
    tokens: int
    # contract v39: the package they're on now (Free when nothing is active); admins are unlimited
    package_id: str | None = None
    package_name: str = ""
    subscription_ends_at: Utc | None = None
    gpu_min_day_limit: float | None = None
    implant_access: bool = False


class AdminUserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    is_admin: bool | None = None
    verified: bool | None = None
    implant_access: bool | None = None
    # contract v39: per-user limits over the package's; sending null clears one
    limit_gpu_min_day: float | None = Field(default=None, ge=0, le=100_000)
    limit_gpu_min_month: float | None = Field(default=None, ge=0, le=10_000_000)
    limit_max_queued: int | None = Field(default=None, ge=0, le=1000)


class AdminUserCreate(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(default="", max_length=200)
    admin: bool = False


# API tokens (contract v27)
TokenScope = Literal["read", "generate", "manage"]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="Shown in the token list, e.g. the script using it",
                      examples=["Nightly renders"])
    scopes: list[TokenScope] = Field(min_length=1, max_length=3, examples=[["read", "generate"]],
                                     description="read = GET, generate = POST/PUT/PATCH, manage = DELETE")
    expires_days: Literal[30, 90, 365] | None = Field(90, description="Days until it stops working; null = never")


class TokenOut(Out):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    status: Literal["active", "expired", "revoked"]
    expires_at: Utc | None = None
    last_used_at: Utc | None = None
    revoked_at: Utc | None = None
    created_at: Utc


class TokenCreatedOut(TokenOut):
    # the only time the full token is ever sent
    token: str


# projects
class Counts(BaseModel):
    scenes: int = 0
    shots: int = 0
    characters: int = 0


class ProjectOut(Out):
    id: str
    title: str
    logline: str
    authoring_mode: str
    aspect_ratio: str
    target_runtime_s: int
    quality: str
    takes_per_shot: int
    overnight: bool
    status: str
    style_bible: str = ""
    settings: dict[str, Any] = {}
    created_at: Utc
    updated_at: Utc
    thumbnail_url: str | None = None
    counts: Counts = Counts()


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    authoring_mode: AuthoringMode
    logline: str = ""
    aspect_ratio: str = "16:9"
    target_runtime_s: int = Field(120, ge=1)
    quality: Quality = "draft"
    takes_per_shot: int = Field(3, ge=1, le=16)
    overnight: bool = False
    brief: str | None = None
    style_bible: str = ""
    settings: dict[str, Any] | None = None


class ProjectPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    authoring_mode: AuthoringMode | None = None
    logline: str | None = None
    aspect_ratio: str | None = None
    target_runtime_s: int | None = Field(None, ge=1)
    quality: Quality | None = None
    takes_per_shot: int | None = Field(None, ge=1, le=16)
    overnight: bool | None = None
    status: ProjectStatus | None = None
    brief: str | None = None
    style_bible: str | None = None
    # merged into project.settings; a key set to null is removed
    settings: dict[str, Any] | None = None


# scenes
class SceneOut(Out):
    id: str
    project_id: str
    order: int
    heading: str
    logline: str
    script_text: str
    summary: str
    time_of_day: str | None
    mood: str
    lighting: str
    source: str
    locked: bool
    version: int
    stale: bool
    location_id: str | None = None
    # P3 review gate: "pending" while the planned shot list waits for approval, else null
    shots_review: str | None = None
    created_at: Utc
    updated_at: Utc


def flush_left(text: str) -> str:
    # Scripts are stored flush-left; pasted screenplays come with centred cues/indented dialogue.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(re.sub(r"^\s+|\s+$", "", line) for line in lines)


ScriptText = Annotated[str, AfterValidator(flush_left)]


class SceneCreate(BaseModel):
    heading: str = ""
    logline: str = ""
    script_text: ScriptText = ""
    after_scene_id: str | None = None


class ScenePatch(BaseModel):
    heading: str | None = None
    logline: str | None = None
    script_text: ScriptText | None = None
    summary: str | None = None
    time_of_day: TimeOfDay | None = None
    mood: str | None = None
    lighting: str | None = None
    locked: bool | None = None
    stale: bool | None = None
    location_id: str | None = None


class ReorderIn(BaseModel):
    scene_ids: list[str]


# generations
class GenerationOut(Out):
    id: str
    target_type: str
    target_id: str
    kind: str
    version: int
    status: str
    prompt: str
    params: dict[str, Any]
    seed: int
    media_url: str | None = None
    media_type: str | None = None
    score: dict[str, Any] | None = None
    note: str | None = None
    created_at: Utc
    approved_at: Utc | None = None
    parent_id: str | None = None
    job_id: str | None = None
    # small WebP preview (/api/media/thumb/{id}); grids use it, the lightbox uses media_url
    thumb_url: str | None = None


class GenerationCreate(BaseModel):
    target_type: TargetType
    target_id: str
    kind: GenKind
    # shots fill an empty prompt from start/end/motion prompts; other targets still need one
    prompt: str = ""
    params: dict[str, Any] = {}


class RegenerateIn(BaseModel):
    mode: Literal["same", "note", "edit"] = "same"
    note: str | None = None
    prompt: str | None = None
    params: dict[str, Any] | None = None


class RejectIn(BaseModel):
    reason: str | None = None


# characters
class FilmRef(BaseModel):
    id: str
    title: str


class CharacterOut(Out):
    id: str
    # None for a Character Studio character (contract v25); it is cast into films through project_cast
    project_id: str | None = None
    name: str
    description: str
    source: str
    locked: bool
    profile: dict[str, Any] = {}
    voice_id: str | None = None
    # how they move on camera: {expressiveness: calm | natural | lively, gestures}
    performance: dict[str, Any] = {}
    studio: bool = False
    approved_portrait: GenerationOut | None = None
    # approved, else the newest finished one: what the studio grid shows while nothing is approved yet
    portrait: GenerationOut | None = None
    panels_ready: int = 0
    board_thumb_url: str | None = None
    # the films using it; filled on the studio routes
    films: list[FilmRef] = []
    created_at: Utc | None = None
    updated_at: Utc | None = None


ProfileText = Annotated[str, Field(max_length=300)]


class CharacterCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "name": "Maya", "description": "A marine biologist in her thirties, short grey hair, weathered wetsuit",
        "profile": {"age": "34", "gender": "female", "nationality": "British"}}]})

    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", description="Looks, clothes, manner. The portrait and sheet prompts start here")
    profile: dict[str, ProfileText] | None = Field(None, description="Identity fields: age, gender, role, accent, "
                                                                     "height, languages...")


class Performance(BaseModel):
    expressiveness: Literal["calm", "natural", "lively"] | None = None
    gestures: str = Field("", max_length=500, description="How they move, e.g. uses hands while explaining")


class CharacterPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    locked: bool | None = None
    # contract v25: the identity block; a key sent blank is cleared
    profile: dict[str, ProfileText] | None = None
    # M12b: a library voice id or "gallery:<id>"; "" clears it
    voice_id: str | None = Field(None, max_length=80)
    performance: Performance | None = None


class PhotoConsent(BaseModel):
    name: str = Field(max_length=120)
    statement: str = Field(max_length=300)


class CharacterFromPhoto(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    media_id: str
    consent: PhotoConsent
    description: str = ""
    # contract v25 own images: as_is keeps the photo itself as the approved portrait, no AI edit
    use: Literal["studio", "as_is"] = "studio"


class OwnImageIn(BaseModel):
    media_id: str
    # required (and checked) when the picture is a photo of a real person
    consent: PhotoConsent | None = None


class SheetCrop(BaseModel):
    panel: str = Field(min_length=1, max_length=40)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class SheetCropsIn(BaseModel):
    crops: list[SheetCrop] = Field(min_length=1, max_length=60)


class CastLinkIn(BaseModel):
    character_id: str


class SheetIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"preset": "essential", "backdrop": "grey"}]})

    preset: Literal["essential", "full", "custom"] = Field(
        "essential", description="essential is about 10 panels (a few minutes); full about 30 (15-20 minutes); "
                                 "custom takes `counts`. Needs an approved portrait first")
    counts: dict[str, int] | None = Field(None, description="custom only: section -> panel count, snapped down "
                                                            "to what the section allows. See /api/character-sheet/catalogue")
    lists: dict[str, list[Annotated[str, Field(max_length=120)]]] | None = Field(
        None, description="Named items: outfits (3), details (4), props (3), accessories (6). Not used by essential")
    backdrop: Literal["grey", "dark"] | None = Field(None, description="One backdrop for every panel")


class SheetPatch(BaseModel):
    titles: dict[str, Annotated[str, Field(max_length=200)]] | None = None
    labels: dict[str, Annotated[str, Field(max_length=200)]] | None = None
    footer: dict[str, Annotated[str, Field(max_length=200)]] | None = None
    ruler: bool | None = None
    backdrop: Literal["grey", "dark"] | None = None
    lists: dict[str, list[Annotated[str, Field(max_length=120)]]] | None = None


class PanelRegenerateIn(BaseModel):
    note: str = Field("", max_length=500)


class BoardIn(BaseModel):
    layout: Literal["dossier", "studio"] = "dossier"


# jobs
class JobOut(Out):
    id: str
    type: str
    status: str
    progress: float
    message: str
    project_id: str | None = None
    generation_id: str | None = None
    gpu: str | None = None
    lane: str = "general"
    attempts: int
    error: str | None = None
    created_at: Utc
    started_at: Utc | None = None
    finished_at: Utc | None = None
    gpu_seconds: float | None = None
    # AI jobs put their output here (thinking, prompts, touched ids); generate jobs their file info
    result: dict[str, Any] | None = None


# AI
AssistAction = Literal["draft_from_idea", "expand", "tighten", "rewrite_tone", "write_dialogue", "suggest_logline"]


class AssistIn(BaseModel):
    action: AssistAction
    idea: str | None = Field(None, max_length=PROMPT_MAX)
    tone: str | None = Field(None, max_length=100)


class WriteMissingIn(BaseModel):
    scene_ids: list[str] | None = None
    # with no empty scenes to fill, insert this many new ones after `after_scene_id`
    after_scene_id: str | None = None
    count: int = Field(1, ge=1, le=10)


class ContinueIn(BaseModel):
    count: int = Field(1, ge=1, le=10)


class SuggestionOut(Out):
    id: str
    project_id: str
    target_type: str
    target_id: str
    field: str
    current_text: str
    proposed_text: str
    action: str
    status: str
    job_id: str | None = None
    created_at: Utc
    resolved_at: Utc | None = None


# locations
class LocationOut(Out):
    id: str
    project_id: str
    name: str
    description: str
    source: str
    locked: bool
    time_of_day_variants: list[str] = []
    approved_establishing: GenerationOut | None = None
    created_at: Utc
    updated_at: Utc


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    time_of_day_variants: list[str] = []


class LocationPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    time_of_day_variants: list[str] | None = None
    locked: bool | None = None


# shots
class ShotOut(Out):
    id: str
    scene_id: str
    project_id: str
    order: int
    shot_type: str
    duration_s: float
    description: str
    camera: str
    prompt: str
    prompt_mode: str
    start_prompt: str
    end_prompt: str
    motion_prompt: str
    character_ids: list[str] = []
    location_id: str | None = None
    seam_in: str
    handoff_text: str
    status: str = "draft"
    stale: bool
    source: str
    locked: bool
    start_frame: GenerationOut | None = None
    start_linked: bool = False
    end_frame: GenerationOut | None = None
    approved_take: GenerationOut | None = None
    takes_count: int = 0
    beats: list[dict[str, Any]] = []
    brand_placements: list[dict[str, Any]] = []
    # set on the advert's closing shot: "ai_packshot" or "logo_reveal" (exact clip, no keyframes);
    # `closing` is the same value under the name the UI reads
    brand_closing: str | None = None
    closing: str | None = None
    brand_placements_locked: bool = False
    camera_rack: dict[str, Any] = {}
    # M12a: {character_id: sheet panel key} picked by hand over the automatic reference choice
    ref_panels: dict[str, str] = {}
    # contract v26: [{character_id, text, pause_after_s?}], spoken in each character's voice and lip-synced;
    # the notice says when a speaker has no voice, so the take falls back to the video model's own audio
    dialogue: list[dict[str, Any]] = []
    dialogue_notice: str | None = None
    # P3 shot cards: generations in flight and kinds whose newest try failed ({id, kind, status, job_id, version})
    activity: list[dict[str, Any]] = []
    created_at: Utc
    updated_at: Utc


class Beat(BaseModel):
    t_start: float = Field(ge=0)
    t_end: float = Field(ge=0)
    prompt: str = Field(max_length=PROMPT_MAX)
    source: Literal["ai", "user", "ai_edited"] = "user"
    locked: bool = False


class ShotCreate(BaseModel):
    shot_type: ShotType | None = None
    duration_s: TakeSeconds = 4.0
    description: str = ""
    camera: str = ""
    character_ids: list[str] | None = None
    location_id: str | None = None
    seam_in: Seam = "cut"
    after_shot_id: str | None = None


class ShotPatch(BaseModel):
    shot_type: ShotType | None = None
    duration_s: TakeSeconds | None = None
    beats: list[Beat] | None = None
    description: str | None = None
    camera: str | None = Field(None, max_length=300)
    # P2 camera rack; setting it also rewrites `camera` as plain words unless that is sent too
    camera_rack: Camera | None = None
    prompt: str | None = None
    prompt_mode: PromptMode | None = None
    start_prompt: ScriptText | None = None
    end_prompt: ScriptText | None = None
    motion_prompt: ScriptText | None = None
    character_ids: list[str] | None = None
    location_id: str | None = None
    seam_in: Seam | None = None
    handoff_text: str | None = None
    locked: bool | None = None
    stale: bool | None = None
    # contract v7: where the brand kit's logo/products appear in this shot
    brand_placements: list["BrandPlacement"] | None = Field(None, max_length=12)
    # contract v25: {character_id: panel key}; "auto" or "" goes back to the automatic pick
    ref_panels: dict[str, str] | None = None
    # contract v26: the whole list of lines; each speaker must be one of the shot's characters
    dialogue: list["DialogueLineIn"] | None = Field(None, max_length=12)


class DialogueLineIn(BaseModel):
    character_id: str = Field(max_length=36)
    text: str = Field(max_length=600)
    pause_after_s: float | None = Field(None, ge=0, le=10)
    language: str | None = Field(None, max_length=12)  # contract v4a: the film's language, kept on edits


class BrandPlacement(BaseModel):
    # extra keys the UI sends along are kept as they are
    model_config = ConfigDict(extra="allow")

    asset_id: str
    asset_type: Literal["logo", "product"]
    surface: str = Field("", max_length=200)
    prominence: Literal["hero", "background"] = "background"
    # "user" placements are locked: AI re-plans only suggest changes to them
    source: Literal["ai", "user"] = "user"


ShotPatch.model_rebuild()


class ShotReorderIn(BaseModel):
    shot_ids: list[str]


class ShotMergeIn(BaseModel):
    shot_ids: list[str] = Field(min_length=2, max_length=20)


class ShotSplitIn(BaseModel):
    # where the cut falls, as a fraction of the shot's duration (and of its description when not given)
    at_ratio: float = Field(0.5, gt=0.05, lt=0.95)
    descriptions: list[str] | None = Field(None, min_length=2, max_length=2)


class ShotExtendIn(BaseModel):
    duration_s: TakeSeconds | None = None
    prompt: str | None = Field(None, max_length=PROMPT_MAX)


class ShotExtendOut(BaseModel):
    shot: "ShotOut"
    job: "JobOut"


class ShotRerenderIn(BaseModel):
    what: Literal["frames", "takes"] = "frames"
    count: int | None = Field(None, ge=1, le=8)
    # frames only: extra generation params (the studio's image model choice)
    params: dict[str, Any] | None = None


class ShotRerenderOut(BaseModel):
    jobs: list["JobOut"]
    # a Continue-seam shot after this one opens on this shot's END: it goes stale once a new END is approved
    linked_next_shot_id: str | None = None


class ShotsReviewIn(BaseModel):
    status: Literal["pending", "approved"] | None = None


class ShotsApproveIn(BaseModel):
    generate_frames: bool = True
    # extra generation params for the frames (the image model picked in the review header)
    params: dict[str, Any] | None = None


class ShotsApproveOut(BaseModel):
    scene: SceneOut
    jobs: list["JobOut"]


class TakesIn(BaseModel):
    count: int | None = Field(None, ge=1, le=16)
    duration_s: TakeSeconds | None = None
    # contract v6; unset = the project's settings (Standard, no smoothing)
    quality: Literal["standard", "hq"] | None = None
    smooth_motion: bool | None = None


class ChunkRegenerateIn(BaseModel):
    prompt: str | None = Field(None, max_length=PROMPT_MAX)
    seed: int | None = Field(None, ge=0)


class TakeEstimate(BaseModel):
    chunks: int
    frames_total: int
    duration_s: float
    long_take: bool
    method: str
    est_gpu_s: int
    est_wall_s: int
    rate_gpu_s_per_output_s: float


class SuggestShotsIn(BaseModel):
    max_shots: int = Field(6, ge=1, le=20)
    review_first: bool = False


class StoryboardIn(BaseModel):
    mode: Literal["scene", "shots"] = "scene"
    scene_ids: list[str] | None = None
    generate_frames: bool = True
    overwrite: bool = False
    continuity: bool | Literal["chain", "none"] = False
    max_shots: int = Field(6, ge=1, le=20)
    # P3: plan the shot list and stop; frames are queued when the user approves it
    review_first: bool = False


class SuggestionResult(BaseModel):
    suggestion: SuggestionOut
    scene: SceneOut | None = None
    character: CharacterOut | None = None
    location: LocationOut | None = None
    shot: ShotOut | None = None


# reel (contract v3)
Transition = Literal["cut", "dissolve", "fade_black"]


class ReelClipOut(BaseModel):
    id: str
    shot_id: str
    scene_id: str
    order: int
    generation_id: str | None
    media_url: str | None = None
    thumb_url: str | None = None
    source_duration_s: float
    trim_in_s: float
    trim_out_s: float
    duration_s: float
    transition_in: str
    transition_s: float
    enabled: bool
    changed: bool


class MezzanineOut(BaseModel):
    status: Literal["fresh", "stale", "missing", "building"]
    generation_id: str | None = None


class ReelSceneOut(BaseModel):
    scene_id: str
    heading: str
    order: int
    duration_s: float
    mezzanine: MezzanineOut
    clips: list[ReelClipOut] = []


class ReelMissingOut(BaseModel):
    shot_id: str
    scene_id: str
    reason: str


class ReelOut(BaseModel):
    id: str
    project_id: str
    duration_s: float
    scenes: list[ReelSceneOut] = []
    missing: list[ReelMissingOut] = []
    last_render: GenerationOut | None = None


class ReelClipPatch(BaseModel):
    trim_in_s: float | None = Field(None, ge=0)
    trim_out_s: float | None = Field(None, ge=0)
    transition_in: Transition | None = None
    transition_s: float | None = Field(None, ge=0, le=10)
    enabled: bool | None = None


class ReelReorderIn(BaseModel):
    clip_ids: list[str]


class AssembleIn(BaseModel):
    quality: Literal["draft"] = "draft"
    # stitch only these scenes (always in film order); empty/None = the whole film
    scene_ids: list[str] | None = None
    title: str | None = Field(None, max_length=200)


class RenderOut(GenerationOut):
    title: str = "Full film"
    scene_range: str = ""
    scene_ids: list[str] = []
    full: bool = True
    duration_s: float | None = None
    clips: int | None = None
    approved: bool = False


class GenerationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ReelEstimateOut(BaseModel):
    clips: int
    duration_s: float
    stale_scenes: int
    est_seconds: float


# Upscale (contract v4)
class UpscaleIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"engine": "best", "target": "1080p"},
                                                              {"engine": "redraw", "target": "2x"}]})

    # engine names (seedvr2, flashvsr, esrgan, zimage) are accepted as aliases
    engine: Literal["best", "fast", "quick", "redraw", "faithful", "seedvr2", "flashvsr", "esrgan", "zimage", "anime",
                    "cartoon"] | None = Field(None, description="Videos: best, fast or quick (empty = the server "
                                                                "default). Images: redraw, faithful, quick, anime "
                                                                "or cartoon (empty = redraw)")
    target: Literal["1080p", "1440p", "4k", "2x", "4x", "2k"] | None = Field(
        None, description="Videos: 1080p, 1440p or 4k (default 1080p). Images: 2x, 4x, 2k or 4k (default 2x)")
    variant: Literal["3b", "7b"] | None = Field(None, description="SeedVR2 size, video engine best only; 7b is "
                                                                  "about twice as slow")
    denoise: float | None = Field(None, ge=0.15, le=0.5, description="Image redraw only: how much may be repainted")
    prompt: str | None = Field(None, max_length=PROMPT_MAX, description="Image redraw only: replaces the source's prompt")


# Media library, Image studio, Templates, Dashboard (contract v5)
ImageAspect = Literal["1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "3:2"]


class MediaItemOut(BaseModel):
    id: str
    workspace_id: str
    kind: str
    origin: str
    title: str
    tags: list[str] = []
    project_id: str | None = None
    project_title: str | None = None
    generation_id: str | None = None
    # the current version's status: queued/generating while a fresh item is still being made
    status: str | None = None
    media_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    media_url: str | None = None
    thumb_url: str | None = None
    created_at: Utc
    updated_at: Utc
    versions_count: int = 1
    # polish P4
    folder_id: str | None = None
    favourite: bool = False
    # set when the current version is an upscale: {"target": "4k", "label": "4K", "engine": "quick"}
    upscale: dict[str, Any] | None = None
    # the version this one descends from (the upload, or the first picture), for "Download original"
    original_generation_id: str | None = None
    # photo catalogue (contract v11)
    rating: int = 0
    flag: str = ""
    label: str = ""
    caption: str = ""
    captured_at: Utc | None = None
    camera: str | None = None
    lens: str | None = None
    focal_mm: float | None = None
    aperture: float | None = None
    shutter_s: float | None = None
    iso: int | None = None
    original_name: str | None = None
    bytes: int | None = None
    source_type: str | None = None  # image/x-raw | image/heic | image/tiff when the original is kept
    edited: bool = False
    album_ids: list[str] = []
    # upload only: this file was already in the catalogue, so nothing new was stored
    duplicate: bool = False
    # audio, once analysed (contract v15): {lufs_i, true_peak_db, flags} for the quality badges
    analysis: dict[str, Any] | None = None
    # what made the current version (kind, engine, voice, text…), so pages can tell speech from songs
    params: dict[str, Any] | None = None
    # contract v37: the studio this is a working part of (character, poetry, story:<id>...); None = a final output
    home: str | None = None


class UpscaleRowOut(BaseModel):
    result: GenerationOut
    source: GenerationOut | None = None
    title: str
    kind: str
    media_id: str | None = None
    project_id: str | None = None
    engine: str | None = None
    target: str | None = None
    label: str | None = None
    width: int | None = None
    height: int | None = None
    status: str
    job: JobOut | None = None
    error: str | None = None


class UpscalePage(BaseModel):
    items: list[UpscaleRowOut]
    next_cursor: str | None = None


class MediaDetailOut(MediaItemOut):
    versions: list[GenerationOut] = []


class MediaPage(BaseModel):
    items: list[MediaItemOut]
    next_cursor: str | None = None


class MediaPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    tags: list[str] | None = Field(None, max_length=30)


MagicMode = Literal["auto", "on", "off"]


# shared wording for the generation request fields below (contract v27 docs)
_SEED = "Fixed seed for repeatable results; with count > 1 each picture adds 1"
_MODEL = "A catalog model id from GET /api/models, or auto. Empty uses the default for the kind"
_MAGIC = "auto, on or off: let the local LLM expand a short prompt first. Empty uses the server default"
_FOLDER = "A Library folder id the new items are filed in"
_PART_OF = ("Mark the result as a working part of something bigger (e.g. story:<id>). It stays out of the "
            "general lists; fetch it by id or with GET /api/media?home=<the same value>")


class ImageGenerateIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "prompt": "A lighthouse on a cliff at dusk, storm clouds, warm window light", "aspect": "16:9", "count": 2}]})

    prompt: str = Field(min_length=1, max_length=PROMPT_MAX, description="What to draw. @mentions of Library pictures "
                                                                   "turn it into a reference-guided composition")
    negative: str | None = Field(None, max_length=PROMPT_MAX, description="Things to keep out of the picture")
    aspect: ImageAspect = Field("1:1", description="Output shape, about 1 megapixel")
    count: int = Field(1, ge=1, le=4, description="Pictures to make; one job each")
    style: str | None = Field(None, description="A style id from GET /api/images/options")
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description=_SEED)
    steps: int | None = Field(None, ge=1, le=60, description="Sampler steps; empty uses the model's own")
    template_id: str | None = Field(None, description="The prompt template this came from, kept for the record")
    title: str | None = Field(None, max_length=300, description="Library title; empty makes one from the prompt")
    model: str | None = Field(None, max_length=60, description=_MODEL)  # contract v6; default Z-Image Turbo
    speed: str | None = Field(None, max_length=30, description="A speed id the chosen model lists")
    # contract v7: palette/look in the prompt, brand pass on the result
    brand_kit_id: str | None = Field(None, description="Apply a brand kit's palette and look")
    magic_prompt: MagicMode | None = Field(None, description=_MAGIC)
    # the dock already ran /prompts/enhance; don't do it again
    prompt_enhanced: bool = Field(False, description="The prompt was already enhanced; skip that step")
    folder_id: str | None = Field(None, description=_FOLDER)  # polish P4 "Generate into…"
    part_of: str | None = Field(None, max_length=80, description=_PART_OF)  # contract v37


class ImageEditIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "source_ids": ["3f2a9c1e-0000-4000-8000-000000000000"], "instruction": "Make it night, keep the lighthouse"}]})

    # the catalog's max_refs is checked in the route, so the message names the model
    source_ids: list[str] = Field(min_length=1, max_length=8, description="Library item or generation ids of "
                                                                         "finished images; the first is edited")
    # may be empty when an image tool does the work (depth map, edge map, LaMa with a mask)
    instruction: str = Field("", max_length=PROMPT_MAX, description="What to change")
    # unlike the generators, a missing value is off: edits from the API and MCP keep the user's exact words
    magic_prompt: MagicMode | None = Field(None, description="Prompt correction for the instruction; empty is off")
    prompt_enhanced: bool = Field(False, description="The instruction was already corrected; skip that step")
    aspect: ImageAspect | None = Field(None, description="Empty keeps the first picture's shape")
    count: int = Field(1, ge=1, le=4)
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description=_SEED)
    title: str | None = Field(None, max_length=300)
    model: str | None = Field(None, max_length=60, description=_MODEL)
    brand_kit_id: str | None = Field(None, description="Also adds the kit's product pictures while there's room")
    folder_id: str | None = Field(None, description=_FOLDER)
    # image tools (contract v30)
    template_id: str | None = Field(None, max_length=80, description="The Edit template this came from; a template "
                                                                     "with a tool (LaMa, IC-Light...) can run on it")
    tool: str | None = Field(None, max_length=30, description="Run this image tool (GET /images/tools) instead of the "
                                                              "template's own")
    engine: Literal["auto", "tool", "edit"] = Field("auto", description="auto: the tool when it can run, else the edit "
                                                                        "model; tool: the tool or 422; edit: never the tool")
    tool_options: dict[str, Any] = Field(default_factory=dict, description="light (none|left|right|top|bottom), "
                                                                           "garment_type (upper|lower|overall), "
                                                                           "low_threshold / high_threshold")
    mask: str | None = Field(None, max_length=12_000_000, description="LaMa: a PNG data URL, white or opaque = "
                                                                      "remove. Without it a Photo Studio selection "
                                                                      "source is used")


VideoAspect = Literal["16:9", "9:16", "1:1", "4:3", "2.39:1"]


class VideoGenerateIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "prompt": "Waves crash against the lighthouse, slow push in", "duration_s": 5, "aspect": "16:9"}]})

    prompt: str = Field(min_length=1, max_length=PROMPT_MAX, description="The scene and its motion")
    model: str | None = Field(None, max_length=60, description=_MODEL)
    duration_s: float = Field(5.0, ge=1, description="Seconds. Past one model pass it renders as a long take in "
                                                     "chunks, up to the server's LONGTAKE_MAX_S")
    # None: the start image's shape (nearest supported), else 16:9
    aspect: VideoAspect | None = Field(None, description="Empty: the start image's shape, else 16:9")
    image_id: str | None = Field(None, description="Start frame: a Library image or generation id")
    # first+last frame (LTX "flf")
    end_image_id: str | None = Field(None, description="Last frame; needs image_id")
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description=_SEED)
    smooth_motion: bool = Field(False, description="Interpolate to a higher frame rate after rendering")
    negative: str | None = Field(None, max_length=PROMPT_MAX)
    title: str | None = Field(None, max_length=300)
    brand_kit_id: str | None = None  # contract v7
    magic_prompt: MagicMode | None = Field(None, description=_MAGIC)
    prompt_enhanced: bool = False
    quality: Literal["standard", "hq"] | None = Field(None, description="Only read when model is auto")
    # polish P2
    camera: Camera | None = Field(None, description="Shot size, angle and move, written into the motion prompt")
    folder_id: str | None = Field(None, description=_FOLDER)
    part_of: str | None = Field(None, max_length=80, description=_PART_OF)


class Img2ImgIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "source_id": "3f2a9c1e-0000-4000-8000-000000000000", "prompt": "Oil painting, thick brush strokes",
        "strength": 0.5}]})

    source_id: str = Field(description="A Library item (uploads too) or a finished image generation id")
    prompt: str = Field("", max_length=PROMPT_MAX, description="The picture you want; may be empty when a style is set")
    style: str | None = Field(None, max_length=40, description="A style id from GET /api/images/options (v31)")
    strength: float = Field(0.45, ge=0.1, le=0.9, description="How far it may move from the source: low keeps the "
                                                              "layout, high keeps little more than the colours. Left "
                                                              "out with a style: the style group's preset")
    model: str | None = Field(None, max_length=60)
    speed: str | None = Field(None, max_length=30)
    count: int = Field(1, ge=1, le=4)
    aspect: Literal["source", "1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "3:2"] = "source"
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    negative: str | None = Field(None, max_length=PROMPT_MAX)
    title: str | None = Field(None, max_length=300)
    brand_kit_id: str | None = None
    magic_prompt: MagicMode | None = None
    prompt_enhanced: bool = False
    folder_id: str | None = None
    template_id: str | None = Field(None, max_length=80, description="The template this came from, for the record")


class ImageControlIn(BaseModel):
    """Depth / Sketch / Pose / Edge to image (contract v31)."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "source_id": "3f2a9c1e-0000-4000-8000-000000000000", "control": "pose",
        "prompt": "A knight in silver armour on a misty hill", "strength": 0.8}]})

    source_id: str = Field(description="A Library item or finished image generation id: a photo, or a map/sketch")
    control: Literal["depth", "canny", "pose", "hed", "scribble"] = Field(
        description="What the picture steers: depth, canny (edges), pose, hed (soft edges, non-commercial) or "
                    "scribble (sketch)")
    use_as_is: bool = Field(False, description="The picture already is the map or sketch; skip extracting one")
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX, description="What to draw")
    style: str | None = Field(None, max_length=40, description="A style id from GET /api/images/options")
    strength: float = Field(0.8, ge=0.65, le=1.0, description="How closely to follow the map")
    aspect: Literal["source", "1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "3:2"] = "source"
    count: int = Field(1, ge=1, le=4)
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description=_SEED)
    negative: str | None = Field(None, max_length=PROMPT_MAX)
    title: str | None = Field(None, max_length=300)
    engine: Literal["auto", "controlnet", "edit"] = Field("auto", description="auto: the ControlNet when installed, "
                                                                              "else Qwen-Image-Edit (looser); "
                                                                              "controlnet: it or 422; edit: never it")
    template_id: str | None = Field(None, max_length=80)
    folder_id: str | None = Field(None, description=_FOLDER)


class VideoGenerateOut(MediaItemOut):
    job: JobOut | None = None
    model_resolved: str | None = None
    magic_prompt: dict | None = None


class BrandImageIn(BaseModel):
    """A Brand template run (contract v35). {tokens} in the texts are filled from the kit on the server."""
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"template_id": "image-brand-vehicle-wrap", "mode": "scene"},
        {"template_id": "image-brand-uniform", "mode": "photo", "source_id": "3f2a9c1e-0000-4000-8000-000000000000",
         "surface": "dark"}]})

    template_id: str = Field(max_length=80, description="A card with target image-brand")
    mode: Literal["photo", "scene"] = Field("photo", description="photo: your picture plus the logo; scene: make "
                                                                 "the scene first, then put the logo on it")
    source_id: str | None = Field(None, description="photo mode: the Library item (or generation) to brand")
    brand_kit_id: str | None = Field(None, description="Empty uses the default kit, or the only one")
    product_id: str | None = Field(None, description="Cards that take a product: one of the kit's product media ids")
    surface: Literal["light", "dark"] | None = Field(None, description="What the logo lands on; dark picks the "
                                                                       "light logo, or asks for light letters")
    instruction: str | None = Field(None, max_length=PROMPT_MAX, description="The logo edit; empty uses the card's")
    scene_prompt: str | None = Field(None, max_length=PROMPT_MAX, description="scene mode: empty uses the card's")
    aspect: ImageAspect | None = None
    count: int = Field(1, ge=1, le=4)
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description=_SEED)
    model: str | None = Field(None, max_length=60, description="The edit model; empty uses the card's")
    title: str | None = Field(None, max_length=300)
    folder_id: str | None = Field(None, description=_FOLDER)


class ImageBatchOut(BaseModel):
    items: list[MediaItemOut]
    jobs: list[JobOut]
    model_resolved: str | None = None  # what "auto" (or the default) turned into
    magic_prompt: dict | None = None
    # /images/edit with a template tool (v30): the tool that ran, or why the edit model ran instead
    tool_resolved: str | None = None
    tool_fallback: str | None = None
    # /images/brand (v35): kit, logo variant and surface the run used
    brand_template: dict | None = None


AudioKind = Literal["song", "music", "sfx"]


class AudioGenerateIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"kind": "song", "prompt": "warm acoustic folk, female vocal", "lyrics": "[verse]\nSalt on the window",
         "duration_s": 60},
        {"kind": "sfx", "prompt": "a heavy wooden door creaks open", "duration_s": 4}]})

    kind: AudioKind = Field(description="song (with lyrics), music (instrumental) or sfx")
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX, description="Genre, mood, instruments; for sfx the sound")
    lyrics: str = Field("", max_length=6000, description="Songs: [verse] / [chorus] sections. "
                                                         "POST /api/audio/lyrics can write them")
    # ISO 639-1, plus "ur-latn" for Roman Urdu; ACE-Step reads anything it doesn't know as "unknown"
    language: str = Field("en", min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")
    # boy / girl / choir / voice are sung by the model and then changed into that voice (app.song_voice)
    vocal: Literal["male", "female", "duet", "none", "boy", "girl", "choir", "voice", "lead_group"] | None = None
    sing_voice_id: str | None = Field(None, max_length=80,
                                      description="vocal=voice (or lead_voice=voice): the Library voice "
                                                  "(or gallery:<id>) to sing in")
    # vocal=lead_group: one child leads, a group of children sings with them or answers. The lyrics say who sings
    # what: [verse: lead], [chorus: group], [chorus: all], and (words in brackets) for the group's answers
    lead_voice: Literal["boy", "girl", "voice"] = "girl"
    group_size: Literal[3, 5] = 3
    bpm: int | None = Field(None, ge=40, le=220)
    category: Literal["music", "instrument", "loop", "sfx", "oneshot"] | None = None
    duration_s: float = Field(30.0, ge=1, le=380, description="Seconds")
    model: str | None = Field(None, max_length=60, description=_MODEL)
    count: int = Field(1, ge=1, le=4)
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description=_SEED)
    steps: int | None = Field(None, ge=1, le=100)
    timbre_ref_id: str | None = Field(None, description="A Library audio item whose sound the result takes on")
    # Arabic-script songs (Urdu, Arabic, Persian...): add the vowel marks to unmarked lines before singing
    aerab: bool = True
    # ACE-Step only: musical key, metre, how closely to follow the tags, and the planning LM
    keyscale: str | None = Field(None, pattern=r"^[A-G][#b]? (major|minor)$")
    time_signature: Literal["2", "3", "4", "6"] | None = None
    guidance: float | None = Field(None, ge=0, le=10)
    think: bool | None = None
    title: str | None = Field(None, max_length=200)
    folder_id: str | None = None


class LyricsIn(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    language: str = Field("en", min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")
    mood: str | None = Field(None, max_length=200)
    style: str | None = Field(None, max_length=200)
    sections: list[Annotated[str, Field(min_length=2, max_length=30)]] = Field(
        default_factory=lambda: ["verse", "chorus", "verse", "chorus", "bridge", "chorus"], min_length=1, max_length=16)
    aerab: bool = True


class LyricsOut(BaseModel):
    lyrics: str
    tags: str
    # Arabic-script languages only (contract v23): lines that got aerab, lines left as written because the
    # marking pass changed their letters, and lines still sparsely marked after the strict retry
    marked: int = 0
    kept: int = 0
    low_coverage: int = 0


class AerabIn(BaseModel):
    lyrics: str = Field(min_length=1, max_length=6000)
    language: str | None = Field(None, max_length=12)


class AerabOut(BaseModel):
    lyrics: str
    marked: int
    kept: int
    low_coverage: int = 0
    language: str | None = None
    complete: bool = True
    remaining_chars: int = 0


class TextAerabIn(BaseModel):
    text: str = Field(min_length=1, max_length=60000)
    # empty or "auto": the letters decide
    language: str | None = Field(None, max_length=12)


class TextAerabOut(BaseModel):
    text: str
    marked: int
    kept: int
    low_coverage: int
    language: str | None
    # false when the time budget ran out first: the text is marked up to that point, `remaining_chars`
    # of it still bare (the jobs route has no budget)
    complete: bool = True
    remaining_chars: int = 0
    # share of the whole text's words that carry a mark
    coverage: float = 1.0


# ---------------------------------------------------------------- audio editor and Score a Clip (contract v15)

FadeCurve = Literal["tri", "qsin", "exp", "log"]
LoudnessTarget = Literal["web", "podcast", "broadcast", "acx"]
ChainPreset = Literal["broadcast", "cinematic", "podcast", "warm", "raw", "radio", "telephone", "hall"]


class Region(BaseModel):
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("a region's end must come after its start")
        return self


class Fade(BaseModel):
    s: float = Field(ge=0, le=30)
    curve: FadeCurve = "tri"


class Chain(BaseModel):
    preset: ChainPreset
    amount: float = Field(100, ge=0, le=100)


class Loudness(BaseModel):
    target: LoudnessTarget


class Marker(BaseModel):
    t: float = Field(ge=0, le=86400)
    label: str = Field("", max_length=40)


class AudioEdit(BaseModel):
    version: Literal[1] = 1
    trim: Region | None = None
    cuts: list[Region] = Field(default_factory=list, max_length=200)
    gain_db: float = Field(0, ge=-24, le=24)
    fade_in: Fade | None = None
    fade_out: Fade | None = None
    speed: float = Field(1.0, ge=0.5, le=2.0)
    pitch: float = Field(0, ge=-12, le=12)
    silence_trim: bool = False
    loop_to: float | None = Field(None, gt=0, le=3600)
    append: list[str] = Field(default_factory=list, max_length=8)
    chain: Chain | None = None
    loudness: Loudness | None = None
    markers: list[Marker] = Field(default_factory=list, max_length=100)


class AudioPreviewIn(BaseModel):
    edit: AudioEdit = Field(default_factory=AudioEdit)
    version: str | None = None


class AudioRenderIn(AudioPreviewIn):
    note: str | None = Field(None, max_length=500)


class MixOriginal(BaseModel):
    keep: bool = True
    gain_db: float = Field(0, ge=-24, le=24)


class MixTrack(BaseModel):
    media_id: str
    offset_s: float = Field(0, ge=0, le=3600)
    gain_db: float = Field(0, ge=-24, le=24)
    mute: bool = False
    fade_in_s: float = Field(0, ge=0, le=30)
    fade_out_s: float = Field(0, ge=0, le=30)
    loop: bool = False
    duck: bool = False


class MixDuck(BaseModel):
    amount_db: float = Field(10, ge=0, le=24)
    under: Literal["original"] = "original"


class MixIn(BaseModel):
    video_id: str
    original: MixOriginal = Field(default_factory=MixOriginal)
    tracks: list[MixTrack] = Field(default_factory=list, max_length=4)
    duck: MixDuck = Field(default_factory=MixDuck)
    loudness: Loudness | None = None


class MixRenderIn(MixIn):
    stems: bool = False
    note: str | None = Field(None, max_length=500)


class TemplateOut(BaseModel):
    id: str
    type: Literal["video", "image", "audio"]
    title: str
    description: str
    thumb: str | None = None
    defaults: dict[str, Any]
    requires_brand: bool = False  # advert templates: the UI asks for a brand kit
    category: str | None = None
    tags: list[str] = []
    examples: dict[str, str] = {}  # placeholder -> sample value (hint text, and what the preview shows)
    preview_url: str | None = None
    # image templates only (v29): which page runs it, and for edits the reference slots it needs
    target: Literal["image-generate", "image-edit", "image-control", "image-brand"] | None = None
    refs_required: int = 0
    ref_labels: list[str] = []
    options: list[dict[str, Any]] = []  # [{slot, label, choices: [{label, value, aspect?}]}]
    # v30: the image tool behind the card ({id, label, engine, licence, noncommercial, fallback, needs_mask})
    tool: dict[str, Any] | None = None
    # v31: Depth / Sketch / Pose to image cards ({type, label, detector, licence, noncommercial, as_is})
    control: dict[str, Any] | None = None
    mine: bool = False  # saved in this workspace (Save as template); can be renamed and deleted
    # v35 Brand templates: kit assets filled in for the user, the default surface, and the scene for "make it for me"
    brand_refs: list[str] = []
    surface: Literal["light", "dark"] | None = None
    scene: dict[str, Any] | None = None


class TemplateStartOut(BaseModel):
    target: Literal["quick", "studio", "image", "image-edit", "image-control", "image-brand", "audio"]
    prefill: dict[str, Any]


class UserTemplateIn(BaseModel):
    """Save as template (contract v31). [slots] in the prompt are what the next person fills in."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "title": "Product on marble", "target": "generate", "aspect": "4:3", "count": 2, "style": "product",
        "prompt": "[product] on white marble, soft window light, a sprig of [herb] beside it"}]})

    title: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    target: Literal["generate", "edit"] = "generate"
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX, description="The prompt, or the instruction for an edit")
    negative: str = Field("", max_length=PROMPT_MAX)
    style: str | None = Field(None, max_length=40)
    model: str | None = Field(None, max_length=60)
    aspect: ImageAspect | None = None
    count: int = Field(1, ge=1, le=4)
    ref_labels: list[str] = Field(default_factory=list, max_length=3,
                                  description="Edit templates: what each picture is, e.g. Person, Garment")
    examples: dict[str, str] = Field(default_factory=dict, description="[slot] -> a sample value, shown as a hint")
    from_media_id: str | None = Field(None, description="The Library result it was saved from; its picture becomes "
                                                        "the card's preview")


class UserTemplatePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    prompt: str | None = Field(None, min_length=1, max_length=PROMPT_MAX)


class DashboardOut(BaseModel):
    recent_projects: list[ProjectOut]
    recent_videos: list[MediaItemOut]
    recent_images: list[MediaItemOut]
    running_jobs: list[JobOut]
    quick_recent: list[dict[str, Any]]


# polish P4: library organisation
FolderKind = Literal["any", "image", "video", "audio", "model"]


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    kind: FolderKind = "any"


class FolderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    # "" moves it to the top level; leave it out to keep the parent
    parent_id: str | None = None
    sort: int | None = None


class FolderOut(Out):
    id: str
    name: str
    parent_id: str | None = None
    kind: str
    sort: int = 0
    item_count: int = 0
    created_at: Utc
    updated_at: Utc


class MoveIn(BaseModel):
    refs: list[str] = Field(min_length=1, max_length=500)
    folder_id: str | None = None  # None: out of every folder


class BatchOut(BaseModel):
    action: str
    done: list[str] = []
    skipped: list[dict[str, str]] = []  # [{ref, reason}]
    jobs: list[JobOut] = []


class FavouriteIn(BaseModel):
    ref: str = Field(min_length=1, max_length=60)
    on: bool | None = None  # None flips it


class FavouriteOut(BaseModel):
    ref: str
    favourite: bool


class FavouritesImportIn(BaseModel):
    refs: list[str] = Field(max_length=2000)


class FavouritesOut(BaseModel):
    refs: list[str]
    imported: int = 0
    skipped: int = 0


class SavedFilterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    query: dict[str, Any] = {}


class SavedFilterPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    query: dict[str, Any] | None = None


class SavedFilterOut(Out):
    id: str
    name: str
    query: dict[str, Any]
    created_at: Utc
    updated_at: Utc


BatchAction = Literal["delete", "download", "upscale", "move", "tag"]


class BatchIn(BaseModel):
    action: BatchAction
    refs: list[str] = Field(min_length=1, max_length=500)
    # move: {folder_id}; tag: {add: [], remove: []}; upscale: {image: {engine, target}, video: {engine, target}}
    options: dict[str, Any] = {}


# ---------------------------------------------------------------- Voice studio (contract v16)

LangCode = Annotated[str, Field(min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")]


class ConsentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=500)


class VoiceDesignIn(BaseModel):
    description: str = Field(min_length=1, max_length=PROMPT_MAX)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    engine: str | None = Field(None, max_length=20)


class VoicePresetIn(BaseModel):
    engine: str = Field("kokoro", max_length=20)
    id: str = Field(min_length=1, max_length=40)


GenderHint = Literal["male", "female", "neutral"]


class VoiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    language: LangCode = "en"
    kind: Literal["clone", "design", "preset"]
    ref_media_id: str | None = None
    ref_text: str = Field("", max_length=2000)
    design: VoiceDesignIn | None = None
    preset: VoicePresetIn | None = None
    notes: str = Field("", max_length=2000)
    gender_hint: GenderHint | None = None
    consent: ConsentIn | None = None
    portrait_media_id: str | None = None
    starred: bool = False
    locked: bool = False


class VoicePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    language: LangCode | None = None
    kind: Literal["clone", "design", "preset"] | None = None
    ref_media_id: str | None = None
    ref_text: str | None = Field(None, max_length=2000)
    design: VoiceDesignIn | None = None
    preset: VoicePresetIn | None = None
    notes: str | None = Field(None, max_length=2000)
    gender_hint: GenderHint | None = None
    consent: ConsentIn | None = None
    portrait_media_id: str | None = None
    starred: bool | None = None
    locked: bool | None = None


class VoiceOut(BaseModel):
    id: str
    name: str
    language: str
    notes: str
    gender_hint: str | None = None
    kind: str
    ref: dict[str, Any] | None = None
    ref_text: str = ""
    design: dict[str, Any] | None = None
    preset: dict[str, Any] | None = None
    portrait_url: str | None = None
    starred: bool = False
    locked: bool = False
    consent: dict[str, Any] | None = None
    created_at: Utc
    updated_at: Utc
    uses: int = 0
    # "short" (under 5 s) or "long" (over 15 s) reference clip: allowed, but cloning is better at 5-15 s
    warnings: list[str] = []


class SpeakDesignIn(BaseModel):
    description: str = Field(min_length=1, max_length=PROMPT_MAX)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)


class SpeakIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"text": "Welcome back to the studio.", "voice_id": "gallery:bm_george", "language": "en"}]})

    text: str = Field(min_length=1, max_length=5000, description="What to say")
    voice_id: str | None = Field(None, description="A voice from GET /api/voices, or gallery:<id> from "
                                                   "GET /api/voices/gallery. Give this or `design`")
    design: SpeakDesignIn | None = Field(None, description="Describe a new voice instead of picking one")
    engine: str = Field("auto", max_length=20, description="auto, or an engine id from GET /api/voices/engines")
    language: LangCode | None = Field(None, description="ISO 639-1; empty uses the voice's language")
    aerab: bool = Field(True, description="Urdu, Arabic, Persian...: add the vowel marks before speaking")
    speed: float = Field(1.0, ge=0.5, le=2.0)
    duration_s: float | None = Field(None, ge=0.5, le=600, description="Fit the speech to this length when the "
                                                                       "engine can")
    steps: int | None = Field(None, ge=1, le=100)
    guidance: float | None = Field(None, ge=0, le=10)
    denoise: bool | None = None
    exaggeration: float | None = Field(None, ge=0, le=2)
    count: int = Field(1, ge=1, le=4)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    title: str | None = Field(None, max_length=200)
    folder_id: str | None = None
    part_of: str | None = Field(None, max_length=80, description=_PART_OF)


class DesignPreviewIn(BaseModel):
    description: str = Field(min_length=1, max_length=PROMPT_MAX)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    engine: str = Field("auto", max_length=20)
    language: LangCode = "en"
    text: str | None = Field(None, max_length=500)
    aerab: bool = True
    folder_id: str | None = None


class SpeakOut(BaseModel):
    items: list[MediaItemOut]
    jobs: list[JobOut]
    engine_resolved: str
    seed: int | None = None
    # OmniVoice design: the attributes it was given, and the words of the description it can't use
    understood: list[str] | None = None
    ignored: list[str] | None = None


# character voices (contract v26)
class CharacterVoiceDesignIn(BaseModel):
    # words added to what the profile says, e.g. "deep, slow"
    extra: str = Field("", max_length=200)
    language: LangCode | None = None
    name: str | None = Field(None, min_length=1, max_length=120)


class CharacterVoiceDesignOut(BaseModel):
    character: CharacterOut
    voice: VoiceOut
    # what was read from the profile, and how OmniVoice took it
    description: str
    understood: list[str] = []
    ignored: list[str] = []


class TestClipIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    # hq: 720p instead of 480p; full: Wan S2V's 20-step path (slow, cleaner hands and teeth)
    quality: Literal["standard", "hq"] = "standard"
    finish: Literal["fast", "full"] | None = None
    lipsync_engine: Literal["ltx", "wan_s2v"] | None = None


class TestClipOut(BaseModel):
    item: MediaItemOut | None = None
    text: str = ""
    panel: str | None = None
    job: JobOut | None = None
    speech_jobs: list[JobOut] = []


# contract v40: 3D Studio
class Model3DViews(BaseModel):
    front: str = Field(description="The front view: a Library media id or a generation id")
    left: str | None = None
    back: str | None = None
    right: str | None = None


class Model3DLogo(BaseModel):
    depth: Literal["thin", "medium", "thick"] = Field("medium", description="3, 6 or 12 % of the logo's width")
    bevel: Literal["none", "soft", "round"] = Field("soft", description="The front and back edges: square, "
                                                                        "chamfered or rounded")
    finish: Literal["gold", "chrome", "steel", "colour"] = Field("gold", description="The sides (and a plain "
                                                                                     "back): metal, or the "
                                                                                     "logo's main colour")
    back: Literal["mirror", "plain"] = Field("mirror", description="mirror shows the artwork through from the "
                                                                   "front; plain gives the back the finish")


class Model3DIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"source_id": "3f2a9c1e-0000-4000-8000-000000000000", "engine": "pixal3d", "quality": "draft"},
        {"prompt": "A brass oil lamp with a glass chimney", "quality": "draft"},
        {"views": {"front": "3f2a9c1e-0000-4000-8000-000000000000", "back": "8c1d2e3f-0000-4000-8000-000000000000"}},
        {"source_id": "3f2a9c1e-0000-4000-8000-000000000000", "better_back": True},
        {"logo_id": "3f2a9c1e-0000-4000-8000-000000000000", "logo": {"depth": "medium", "bevel": "round",
                                                                     "finish": "gold", "back": "mirror"}},
    ]})

    # exactly one of these five says where the model starts from
    source_id: str | None = Field(None, description="A finished picture: a Library media id or a generation id")
    prompt: str | None = Field(None, max_length=PROMPT_MAX, description="Describe one object; a picture of it is made "
                                                                  "first, then the model")
    character_id: str | None = Field(None, description="A character: its turnaround panels as views, else its "
                                                       "full-body picture or portrait")
    views: Model3DViews | None = Field(None, description="Two to four views of one object, same scale, square "
                                                         "framing works best. Pixal3D only")
    logo_id: str | None = Field(None, description="A logo, sign, icon or sticker: its outline is extruded on the "
                                                  "CPU, the art goes on the front. engine and quality don't apply")
    logo: Model3DLogo | None = Field(None, description="A logo's depth, bevel, finish and back")
    better_back: bool | None = Field(None, description="Make side and back views of the picture first, then the "
                                                       "model from all three (Pixal3D, +1-2 min). Empty: on for "
                                                       "a character without a turnaround, off for a picture")
    engine: Literal["pixal3d", "trellis2"] = Field("pixal3d", description="pixal3d stays true to the picture; "
                                                                          "trellis2 is the general model")
    quality: Literal["draft", "final"] = Field("final", description="draft is about half the time, less detail")
    remove_background: bool = Field(True, description="Cut the subject out first. Off only helps a picture that "
                                                      "already has a transparent background")
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    title: str | None = Field(None, max_length=300)
    folder_id: str | None = Field(None, description=_FOLDER)
    part_of: str | None = Field(None, max_length=80, description=_PART_OF)
    # a text start's picture
    image_model: str | None = Field(None, max_length=60, description="The image model for a text start; empty "
                                                                     "uses the default")
    brand_kit_id: str | None = Field(None, description="A text start's picture takes the kit's palette and look")
    magic_prompt: MagicMode | None = Field(None, description="A text start: let the local LLM expand the prompt "
                                                             "first. Off unless asked")
    prompt_enhanced: bool = Field(False, description="The prompt was already enhanced; skip that step")

    @model_validator(mode="after")
    def _one_start(self):
        if self.prompt is not None and not self.prompt.strip():
            self.prompt = None
        given = [k for k in ("source_id", "prompt", "character_id", "views", "logo_id") if getattr(self, k)]
        if len(given) != 1:
            raise ValueError("Give exactly one of source_id, prompt, character_id, views or logo_id")
        return self


class Model3DOut(BaseModel):
    item: MediaItemOut
    job: JobOut
    model_resolved: str
    start: Literal["picture", "text", "character", "views", "logo"] = "picture"
    # set when remove_background was asked off but the picture has nothing transparent to go by
    background_note: str | None = None
    # set when the start forced Pixal3D (several views) over the engine that was asked for
    engine_note: str | None = None
    # a text start: the picture being made first (a working part, home "3d") and its job
    picture: MediaItemOut | None = None
    picture_job: JobOut | None = None
    # the views a multi-view model is made from, in front, left, back, right order
    views_used: list[str] | None = None
    # better back: the side and back views being made first (working parts, home "3d") and their jobs
    better_back: bool = False
    # set when better back was asked for but couldn't be offered, so the one picture was used
    better_back_note: str | None = None
    back_views: list[MediaItemOut] | None = None
    back_jobs: list[JobOut] | None = None
    # a logo without transparency: the cut-out made first on the image GPU, and its job
    cutout: MediaItemOut | None = None
    cutout_job: JobOut | None = None


class Turntable3DOut(BaseModel):
    item: MediaItemOut  # the turntable video, a final output in the Library
    model: MediaItemOut  # the model, with its new thumb_url when the turntable's first frame became it
    transcoded: bool  # False when it stayed WebM because ffmpeg couldn't make the MP4


class Shots3DOut(BaseModel):
    # in the order they were sent; Ad Studio working parts (home "ad"), so only its pickers list them
    items: list[MediaItemOut]
    ids: list[str]
