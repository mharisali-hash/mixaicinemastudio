"""Job lanes: which GPU (ComfyUI instance) a job belongs on.

image   -> stills, image edits, image upscales and 3D models (COMFY_IMAGE_URLS)
video   -> takes, long takes, renders, video upscales (COMFY_VIDEO_URLS)
audio   -> songs, music, sound effects, speech and voice transcripts; served by both GPU workers, each on
           its own ComfyUI / GPU
general -> LLM work, ffmpeg assembly / branding, exports, autopilot orchestration, Photo Studio renders and
           looks on video, podcast writing / mixing / publishing, the AI video podcast's orchestration and cut
           (CPU only; its lip-sync takes are ordinary generate jobs on the video lane), Ad Studio's concepts
           and layouts (its photos are generate jobs on the image lane), 3D Studio's logo badges (model3d_logo:
           numpy; its cut-out is a generate job on the image lane)

Kept free of app imports on purpose: migration 0009 backfills with the same classifier.
"""
import re

LANES = ("image", "video", "audio", "general")

IMAGE_KINDS = frozenset({"portrait", "sheet_view", "keyframe_start", "keyframe_end", "keyframe_mid",
                         "establishing", "image"})
VIDEO_KINDS = frozenset({"take", "tile", "render", "video"})
AUDIO_KINDS = frozenset({"song", "music", "sfx", "speech"})
# 3D Studio (contract v40): Pixal3D / TRELLIS.2 run in the image GPU's ComfyUI
MODEL_KINDS = frozenset({"model3d"})
GPU_LANES = ("image", "video")
# TODO: let an idle lane borrow the other GPU (a night of image batches leaves the video GPU unused)
# job types that always run on the video GPU whatever their generation says (SeedVR2 / FlashVSR segments)
VIDEO_JOB_TYPES = frozenset({"upscale"})
# speech-to-text on a voice's reference clip: a GPU model, but either card will do; stem splits likewise
# a story's narration (contract v28) speaks and checks the whole book in one job; a Poetry Short's line
# timing (contract v33) splits the vocals off its song and listens to them
AUDIO_JOB_TYPES = frozenset({"voice_transcribe", "transcribe", "voice_sample", "audio_stems", "story_audio",
                             "poetry_timing", "audio_remix"})
# a dub_stage job's lane depends on its payload's stage, so it is set when the job is made (dub_lane)
DUB_GPU_STAGES = frozenset({"separate", "transcribe", "render", "verify"})


def dub_lane(stage: str) -> str:
    return "audio" if stage in DUB_GPU_STAGES else "general"


def lane_for(job_type: str, gen_kind: str | None = None) -> str:
    if job_type in VIDEO_JOB_TYPES:
        return "video"
    if job_type in AUDIO_JOB_TYPES:
        return "audio"
    if job_type == "generate":
        if gen_kind in IMAGE_KINDS or gen_kind in MODEL_KINDS:
            return "image"
        if gen_kind in VIDEO_KINDS:
            return "video"
        if gen_kind in AUDIO_KINDS:
            return "audio"
    # scene_text generations, ai_*, reel_assemble, brand_*, media_zip, autopilot, photo_render, look_video...
    return "general"


def parse_lanes(value: str | None) -> tuple[str, ...]:
    """'image,video' -> ('image', 'video'); empty or 'all' means every lane.

    Also takes '-', '_' and ':' as separators because systemd instance names can't hold commas
    (mixai-worker@image-video.service)."""
    raw = [p for p in re.split(r"[\s,+:_-]+", (value or "").lower()) if p]
    if not raw or raw == ["all"]:
        return LANES
    bad = [p for p in raw if p not in LANES]
    if bad:
        raise ValueError(f"unknown lane(s) {', '.join(bad)} (expected {', '.join(LANES)} or all)")
    return tuple(dict.fromkeys(raw))


def gpu_lane(lanes) -> str:
    """The GPU a worker renders audio on: its own image or video lane; an all-lanes worker uses the image one."""
    return next((lane for lane in (lanes or ()) if lane in GPU_LANES), "image")
