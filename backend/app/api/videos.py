"""Create Video (contract v6): one prompt (plus an optional start image) to one clip in the Library."""
import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import brand
from app import camera as cam
from app import mentions as mn
from app import library as lib
from app import longtake
from app import models_catalog as mc
from app import rights
from app import prompt_enhance
from app.config import get_settings
from app.db import get_db
from app.drivers.comfy import HQ_SIZES, VIDEO_SIZES, WAN_SIZES
from app.models import Generation, Job, MediaItem, new_id
from app.schemas import VideoGenerateIn, VideoGenerateOut
from app.security import CurrentUser, require_editor
from app.services import enqueue_generation, job_out

router = APIRouter(prefix="/videos", tags=["videos"])


def _start_image(db: Session, workspace_id: str, image_id: str, what: str = "Start image") -> Generation:
    """A Library image (MediaItem id, uploads included) or any finished image generation id."""
    item = db.get(MediaItem, image_id)
    if item is not None and item.workspace_id == workspace_id:
        g = db.get(Generation, item.generation_id) if item.generation_id else None
    else:
        g = db.get(Generation, image_id)
        if g is not None and g.workspace_id != workspace_id:
            g = None
    if g is None:
        raise HTTPException(404, f"{what} not found")
    if not (g.media_type or "").startswith("image/") or g.status not in lib.FINISHED or not g.file_path:
        raise HTTPException(422, f"The {what.lower()} isn't a finished image")
    return g


def _aspect_of(g: Generation) -> str:
    """The supported video aspect closest to the start image, so a portrait photo stays portrait."""
    w, h = lib._image_dims(g)
    if not w or not h:
        return "16:9"
    ratios = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "2.39:1": 2.39}
    return min(ratios, key=lambda a: abs(math.log((w / h) / ratios[a])))


def _size(m: mc.Model, aspect: str) -> tuple[int, int]:
    table = {"wan22_t2v": WAN_SIZES, "ltx23_hq": HQ_SIZES}.get(m.id, VIDEO_SIZES)
    return table.get(aspect, table["16:9"])


@router.post("/generate", response_model=VideoGenerateOut, status_code=202)
def generate_video(body: VideoGenerateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    prompt = body.prompt.strip()
    if not mn.plain(prompt).strip():
        raise HTTPException(422, "A prompt is required")
    from app.api.library import generate_into

    into = generate_into(db, cur.workspace_id, body.folder_id, "video")
    # the video models take no reference pictures: mentions become names plus a short description
    mentioned = mn.resolve(db, cur.workspace_id, prompt, budget=0)
    prompt = mentioned.prompt
    m, chosen = rights.choose(db, body.model, "video", prompt, quality=body.quality)
    mc.check_video(m, has_image=bool(body.image_id or body.end_image_id), duration_s=body.duration_s,
                   smooth_motion=body.smooth_motion)
    if body.end_image_id:
        if not body.image_id:
            raise HTTPException(422, "An end image needs a start image too")
        if "flf" not in m.capabilities:
            raise HTTPException(422, f"{m.label} can't end on a given image; pick LTX-2.3")
    top = get_settings().longtake_max_s
    if body.duration_s > top:
        raise HTTPException(422, f"duration_s must be at most {top:g} seconds")
    long_take = m.id == "ltx23_distilled" and longtake.is_long(body.duration_s)
    if long_take and body.smooth_motion:
        raise HTTPException(422, f"Smooth motion works on clips up to {mc.SINGLE_PASS_S:.1f} s; "
                                 "turn it off or make the clip shorter")
    start = _start_image(db, cur.workspace_id, body.image_id) if body.image_id else None
    end = _start_image(db, cur.workspace_id, body.end_image_id, "End image") if body.end_image_id else None

    aspect = body.aspect or (_aspect_of(start) if start is not None else "16:9")
    w, h = _size(m, aspect)
    fps = mc.WAN_FPS if m.id == "wan22_t2v" else longtake.FPS
    frames = mc.video_frames(m, body.duration_s, fps)
    # Wan is capped at its trained 81 frames, so report what it will really make
    duration = round((frames - 1) / fps, 3) if m.id == "wan22_t2v" else body.duration_s
    params = {"model": m.id, "aspect_ratio": aspect, "duration_s": duration, "fps": fps,
              "num_frames": frames, "width": w, "height": h,
              "smooth_motion": body.smooth_motion, "user_prompt": mn.plain(body.prompt).strip(),
              "created_by": {"user_id": cur.id, "flow": "video_generate"}, **chosen,
              "magic_prompt": prompt_enhance.marker(body.magic_prompt, body.prompt_enhanced, "video",
                                                    brand_kit_id=body.brand_kit_id)}
    if m.id == "ltx23_hq":
        params["quality"] = "hq"
    if start is not None:
        params["first_frame_id"] = start.id
    if end is not None:
        # long clips pin it on the last chunk (longtake.run_take)
        params["last_frame_id"] = end.id
    if body.negative:
        params["negative"] = body.negative.strip()
    if long_take:
        longtake.init_params(params)
    if mentioned.mentions:
        params["mentions"] = mentioned.mentions
    if body.camera is not None and not body.camera.is_empty():
        params["camera"] = body.camera.model_dump(exclude_none=True)
        prompt = cam.compose(prompt, body.camera)

    prompt, params = brand.apply_to_request(db, cur.workspace_id, body.brand_kit_id, prompt, params)
    title = (body.title or "").strip() or lib.short_title(mn.plain(body.prompt))
    item = MediaItem(id=new_id(), workspace_id=cur.workspace_id, kind="video", origin="generated", title=title,
                     tags=[], width=w, height=h, duration_s=params["duration_s"], folder_id=into,
                     home=lib.home_of(body.part_of))
    db.add(item)
    db.flush()
    g = enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="media",
                           target_id=item.id, kind="video", prompt=prompt, params=params, seed=body.seed)
    item.generation_id = g.id
    job = db.get(Job, g.job_id)
    db.commit()
    return VideoGenerateOut(**lib.item_out(db, item).model_dump(), job=job_out(job), model_resolved=m.id,
                            magic_prompt=params["magic_prompt"])
