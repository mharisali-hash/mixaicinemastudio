"""Podcast Studio request/response shapes (contract v20)."""
from typing import Literal

from pydantic import BaseModel, Field

from app.limits import PROMPT_MAX
from app.dialogue_schemas import Counts
from app.schemas import JobOut, LangCode, Utc

Format = Literal["interview", "conversation", "debate", "solo", "news", "storytelling", "qa"]
Role = Literal["host", "cohost", "guest", "narrator"]


class Host(BaseModel):
    id: str | None = Field(None, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    role: Role = "host"
    persona: str = Field("", max_length=PROMPT_MAX)
    voice_id: str | None = Field(None, max_length=120)
    me: bool = False


class HostOut(Host):
    id: str


class Ad(BaseModel):
    id: str | None = Field(None, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["jingle", "sponsor"] = "jingle"
    media_id: str | None = None
    text: str | None = Field(None, max_length=3000)
    host_id: str | None = Field(None, max_length=40)


class AdOut(Ad):
    id: str


class ShowMusic(BaseModel):
    intro_media_id: str | None = None
    outro_media_id: str | None = None
    bed_media_id: str | None = None


class ShowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    language: LangCode = "en"
    description: str = Field("", max_length=4000)
    author: str = Field("", max_length=200)
    cover_media_id: str | None = None
    hosts: list[Host] = Field(default_factory=list, max_length=4)
    music: ShowMusic = Field(default_factory=ShowMusic)
    ads: list[Ad] = Field(default_factory=list, max_length=100)


class ShowPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    language: LangCode | None = None
    description: str | None = Field(None, max_length=4000)
    author: str | None = Field(None, max_length=200)
    cover_media_id: str | None = None
    hosts: list[Host] | None = Field(None, max_length=4)
    music: ShowMusic | None = None
    ads: list[Ad] | None = Field(None, max_length=100)


class ShowOut(BaseModel):
    id: str
    name: str
    description: str
    author: str
    language: str
    cover_media_id: str | None = None
    cover_url: str | None = None
    hosts: list[HostOut]
    music: ShowMusic
    ads: list[AdOut]
    episodes: int = 0
    created_at: Utc
    updated_at: Utc


class Intro(BaseModel):
    media_id: str | None = None
    lead_s: float = Field(4.0, ge=0, le=60)
    overlap_s: float = Field(3.0, ge=0, le=30)
    gain_db: float = Field(0.0, ge=-40, le=12)


class Outro(BaseModel):
    media_id: str | None = None
    overlap_s: float = Field(2.0, ge=0, le=30)
    gain_db: float = Field(0.0, ge=-40, le=12)
    max_s: float = Field(30.0, ge=1, le=600)


class Bed(BaseModel):
    media_id: str | None = None
    gain_db: float = Field(-20.0, ge=-60, le=0)


class EpisodeSettings(BaseModel):
    intro: Intro = Field(default_factory=Intro)
    outro: Outro = Field(default_factory=Outro)
    bed: Bed = Field(default_factory=Bed)
    duck_db: float = Field(12.0, ge=0, le=40)
    gap_s: float = Field(0.35, ge=0, le=10)
    chapter_gap_s: float = Field(1.0, ge=0, le=10)
    loudness: Literal["stereo", "mono", "none"] = "stereo"


class Chapter(BaseModel):
    id: str | None = Field(None, max_length=40)
    title: str = Field(min_length=1, max_length=200)


class ChapterOut(BaseModel):
    id: str
    title: str


class NoteChapter(BaseModel):
    title: str = Field(max_length=200)
    start: float = Field(ge=0)


class Notes(BaseModel):
    description: str = Field("", max_length=8000)
    key_points: list[str] = Field(default_factory=list, max_length=20)
    chapters: list[NoteChapter] = Field(default_factory=list, max_length=100)


class SourcesPatch(BaseModel):
    topic: str | None = Field(None, max_length=PROMPT_MAX)
    text: str | None = Field(None, max_length=200_000)


class DocOut(BaseModel):
    id: str
    name: str
    type: str
    chars: int
    pages: int | None = None
    truncated: bool = False


class SourcesOut(BaseModel):
    topic: str = ""
    text: str = ""
    docs: list[DocOut] = []
    has_script: bool = False


class MixRecord(BaseModel):
    media_id: str | None = None
    generation_id: str
    url: str | None = None
    duration_s: float | None = None
    built_at: str | None = None
    stale: bool = False
    timeline: list[dict] = []
    chapters: list[dict] = []


class FileRecord(BaseModel):
    media_id: str | None = None
    generation_id: str
    url: str | None = None
    format: str | None = None
    aspect: str | None = None
    burned: bool | None = None
    built_at: str | None = None


class Outputs(BaseModel):
    mix: MixRecord | None = None
    publish: FileRecord | None = None
    video: FileRecord | None = None


class EpisodeOut(BaseModel):
    id: str
    show_id: str | None = None
    show_name: str | None = None
    title: str
    format: str
    language: str
    target_minutes: float
    step: int
    cover_media_id: str | None = None
    cover_url: str | None = None
    sources: SourcesOut
    hosts: list[HostOut]
    chapters: list[ChapterOut]
    script_id: str | None = None
    settings: EpisodeSettings
    notes: Notes | None = None
    outputs: Outputs
    jobs: dict[str, JobOut] = {}
    words: int = 0
    minutes: float = 0
    counts: Counts
    created_at: Utc
    updated_at: Utc


class EpisodeSummary(BaseModel):
    id: str
    show_id: str | None = None
    title: str
    format: str
    language: str
    target_minutes: float
    step: int
    minutes: float = 0
    has_mix: bool = False
    published: bool = False
    created_at: Utc
    updated_at: Utc


class EpisodeIn(BaseModel):
    title: str = Field("New episode", min_length=1, max_length=200)
    show_id: str | None = None
    format: Format = "conversation"
    language: LangCode | None = None
    target_minutes: float = Field(5.0, ge=0.5, le=120)
    hosts: list[Host] | None = Field(None, max_length=4)
    topic: str = Field("", max_length=PROMPT_MAX)


class EpisodePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    show_id: str | None = None
    format: Format | None = None
    language: LangCode | None = None
    target_minutes: float | None = Field(None, ge=0.5, le=120)
    step: int | None = Field(None, ge=1, le=6)
    cover_media_id: str | None = None
    sources: SourcesPatch | None = None
    hosts: list[Host] | None = Field(None, max_length=4)
    chapters: list[Chapter] | None = Field(None, max_length=60)
    settings: EpisodeSettings | None = None
    notes: Notes | None = None


class ScriptTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class EditIn(BaseModel):
    op: Literal["rewrite_chapter", "shorter", "livelier"]
    line_ids: list[str] | None = Field(None, max_length=400)
    chapter_id: str | None = None
    note: str = Field("", max_length=PROMPT_MAX)


class RecordIn(BaseModel):
    line_id: str
    media_id: str


class InsertIn(BaseModel):
    kind: Literal["ad", "music", "break"]
    after_line_id: str | None = None
    ad_id: str | None = None
    media_id: str | None = None
    seconds: float | None = Field(None, ge=0.1, le=30)
    speaker_id: str | None = None
    text: str | None = Field(None, max_length=3000)


class PublishIn(BaseModel):
    format: Literal["mp3", "m4a"] = "mp3"
    cover_media_id: str | None = None
    notes: bool = True


class VideoIn(BaseModel):
    aspect: Literal["16:9", "1:1"] = "16:9"
    burn_subtitles: bool = True
    cover_media_id: str | None = None


class OutputsOut(BaseModel):
    mix: MixRecord | None = None
    publish: FileRecord | None = None
    video: FileRecord | None = None
    notes: Notes | None = None
    srt_url: str | None = None
    notes_url: str | None = None
