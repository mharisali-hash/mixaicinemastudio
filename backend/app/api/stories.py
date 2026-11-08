"""Story books (contract v28): a narrated book and its read-along videos, for BlogCreatorPro over the token API."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app import stories
from app.db import get_db, release
from app.models import Job
from app.schemas import JobOut
from app.security import CurrentUser, get_current_user, require_editor, require_feature
from app.services import job_out

router = APIRouter(prefix="/stories", tags=["stories"], dependencies=[Depends(require_feature("stories"))])

Key = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
MEDIA_TYPES = {".mp3": "audio/mpeg", ".mp4": "video/mp4", ".srt": "application/x-subrip"}


class StoryDesign(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)


class StoryVoice(BaseModel):
    design: StoryDesign | None = None
    preset: str | None = Field(None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def one_kind(self):
        if (self.design is None) == (self.preset is None):
            raise ValueError("give a speaker either design or preset")
        return self


class StoryLine(BaseModel):
    speaker: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=1000)
    emotion: Literal["happy", "sad", "excited", "scared", "angry", "whisper", "calm"] | None = None
    pause_after: float = Field(0, ge=0, le=10, description="Seconds of silence after this line (v28d); the "
                                                           "line's end time leaves it out, its page's span keeps it")


class StoryPage(BaseModel):
    key: str = Key
    lines: list[StoryLine] = Field(min_length=1, max_length=60)


class StoryMusic(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    model: Literal["ace15", "sa3"] = "ace15"
    level: Literal["off", "soft", "normal"] = "soft"


class StoryAudioIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "language": "en", "engine": "voxcpm",
        "cast": {"narrator": {"design": {"description": "warm, gentle British storyteller, female, 40s", "seed": 123}},
                 "Pip": {"design": {"description": "small excited boy, about 6, high bright voice", "seed": 456}}},
        "pages": [{"key": "p1", "lines": [{"speaker": "narrator", "text": "Pip ran to the window."},
                                          {"speaker": "Pip", "text": "\"It's snowing!\"", "emotion": "excited"}]}],
        "music": {"prompt": "soft lullaby, music box and strings, calm, no vocals", "model": "ace15", "level": "soft"},
        "gap_line": 0.35, "gap_page": 1.2, "check": True}]})

    language: str = Field("en", min_length=2, max_length=12, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")
    engine: str = Field("voxcpm", max_length=20,
                        description="voxcpm (default), chatterbox, kokoro, or omnivoice where the server allows it")
    cast: dict[str, StoryVoice] = Field(min_length=1, max_length=13)
    pages: list[StoryPage] = Field(min_length=1, max_length=200)
    music: StoryMusic | None = None
    gap_line: float = Field(0.35, ge=0, le=5)
    gap_page: float = Field(1.2, ge=0, le=10)
    check: bool = False
    # Arabic-script lines (ur, ar, fa, ps, sd, pnb) with no marks get aerab before they're spoken
    aerab: bool = True

    @model_validator(mode="after")
    def speakers_known(self):
        if any(not k.strip() or len(k) > 60 for k in self.cast):
            raise ValueError("cast names must be 1 to 60 characters")
        keys = [p.key for p in self.pages]
        if len(set(keys)) != len(keys):
            raise ValueError("page keys must be unique")
        for p in self.pages:
            for ln in p.lines:
                if ln.speaker not in self.cast:
                    raise ValueError(f"page {p.key}: speaker '{ln.speaker}' isn't in the cast")
        return self


class StoryPicture(BaseModel):
    key: str = Key
    media_id: str = Field(min_length=1, max_length=64,
                          description="A finished Library picture or video (its item id); a clip is fitted to the page")
    captions: bool = Field(True, description="False: no caption box on this page (the .srt still has its lines)")
    effect: Literal["welcome", "pop", "reveal", "fade"] | None = Field(
        None, description="How the page comes in; none is the usual slow zoom (v28d)")


class TitleCard(BaseModel):
    media_id: str | None = Field(None, max_length=64, description="The cover; the first page's picture when left out")
    title: str = Field(min_length=1, max_length=200)
    seconds: float = Field(4.0, ge=1, le=15)


class EndCard(BaseModel):
    text: str = Field("The End", min_length=1, max_length=200)
    seconds: float = Field(5.0, ge=1, le=15)


class StoryVideoIn(BaseModel):
    audio_job: str = Field(min_length=1, max_length=64)
    aspect: Literal["16:9", "9:16"] = "16:9"
    use_music: bool = True
    audio_from: Literal["full", "pages"] = Field(
        "full", description="pages: the sent pages' own audio joined in the order sent, gap_page between, the "
                            "bed laid under the new track (v28d)")
    pages: list[StoryPicture] = Field(min_length=1, max_length=200)
    title_card: TitleCard | None = None
    end_card: EndCard | None = None
    captions: bool = True
    max_seconds: float | None = Field(None, ge=5, le=4 * 3600)


class StoryJobOut(BaseModel):
    job: JobOut


@router.post("/audio", response_model=StoryJobOut, status_code=202, summary="Narrate a story book",
             description="One job speaks every line (one seed per speaker), writes a voice-only MP3 per page, "
                         "full.mp3 at -16 LUFS and, with music, full_music.mp3. Poll GET /api/jobs/{id}; "
                         "download with GET /api/stories/{job_id}/files/{name}.")
def story_audio(body: StoryAudioIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    job = stories.queue_audio(db, cur, body.model_dump(mode="json"))
    db.commit()
    return {"job": job_out(job)}


@router.post("/video", response_model=StoryJobOut, status_code=202, summary="Make a read-along video",
             description="One ffmpeg job: each page's picture or video clip for as long as its audio, captions per "
                         "line, chapters per page. 16:9 or a 9:16 Short (max_seconds keeps whole pages from the "
                         "start).")
def story_video(body: StoryVideoIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    job = stories.queue_video(db, cur, body.model_dump(mode="json"))
    db.commit()
    return {"job": job_out(job)}


@router.get("/{job_id}/files/{name}", summary="Download a story file",
            description="`name` is a file named in the job's result (page MP3s, full.mp3, full_music.mp3, the "
                        "video and its .srt). Files are deleted after 7 days.")
def story_file(job_id: str, name: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    job = db.get(Job, job_id)
    # one 404 for someone else's job, a job that isn't a story and a name it never made
    if job is None or job.workspace_id != cur.workspace_id or job.type not in (stories.AUDIO_JOB, stories.VIDEO_JOB) \
            or job.status != "done" or name not in stories.file_names(job.result):
        raise HTTPException(404, "File not found")
    folder = stories.story_dir(job.workspace_id, job.id).resolve()
    path = (folder / name).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise HTTPException(404, "This file is gone (story files are kept for 7 days)")
    release(db)
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower()), filename=name,
                        headers={"Cache-Control": "private, no-store"})
