"""Media library, Image studio, Prompt templates and the Home dashboard (contract v5).

Registered before app.api.media: GET /media/{id} here takes one path segment, while stored files are
always served as /media/workspaces/..., so the two never compete for a URL.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import brand
from app import brand_templates as bt
from app import control as ctl
from app import image_tools as it
from app import library as lib
from app import mentions as mn
from app import models_catalog as mc
from app import rights
from app import organise as org
from app import prompt_enhance, uploads
from app import visibility as vis
from app import styles as st
from app.ai_jobs import rewrite_prompt_from_note
from app.db import get_db, release
from app.models import Generation, Job, MediaItem, Project, UserTemplate, new_id, utcnow
from app.schemas import (
    BrandImageIn, DashboardOut, ImageBatchOut, ImageControlIn, ImageEditIn, ImageGenerateIn, Img2ImgIn, JobOut,
    MediaDetailOut, MediaItemOut, MediaPage, MediaPatch, RegenerateIn, TemplateOut, TemplateStartOut, UpscalePage,
    UserTemplateIn, UserTemplatePatch,
)
from app.openapi import multipart
from app.security import CurrentUser, get_current_user, require_editor
from app.services import enqueue_generation, job_out, new_seed, project_out

router = APIRouter(tags=["library"])

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def _owned_item(db: Session, media_id: str, workspace_id: str) -> MediaItem:
    item = db.get(MediaItem, media_id) if _UUID.match(media_id) else None
    if item is None or item.workspace_id != workspace_id:
        if _UUID.match(media_id) and lib.project_row(db, workspace_id, media_id) is not None:
            raise HTTPException(409, "This is a project result; change it inside its project")
        raise HTTPException(404, "Media not found")
    return item


# ---------------------------------------------------------------- library

@router.get("/media", response_model=MediaPage)
def list_media(
    kind: Literal["image", "video", "audio", "model"] | None = None,
    origin: Literal["generated", "upload", "project"] | None = None,
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=40),
    project_id: str | None = None,
    include: str | None = None,
    folder_id: str | None = None,
    favourite: bool = False,
    limit: int = Query(40, ge=1, le=lib.MAX_LIMIT),
    cursor: str | None = None,
    home: str | None = Query(None, max_length=120, description="Only the working parts of this studio "
                                                               "(contract v37); any = every studio's parts"),
    include_home: str | None = Query(None, max_length=400, description="Comma list of studios whose parts are "
                                                                       "listed too, e.g. character"),
    db: Session = Depends(get_db),
    cur: CurrentUser = Depends(get_current_user),
):
    if folder_id:
        _folder(db, cur.workspace_id, folder_id)
    sc = org.scope(db, cur.workspace_id, cur.id, folder_id, favourite)
    items, nxt = lib.list_media(db, cur.workspace_id, kind=kind, origin=origin, q=q, tag=tag, project_id=project_id,
                                include_project="project" in (include or "").split(","), limit=limit, cursor=cursor,
                                scope=sc, want=vis.wanted(cur), home=lib.home_of(home),
                                include_home=[h for h in map(lib.home_of, (include_home or "").split(",")) if h])
    return MediaPage(items=org.annotate(db, cur.workspace_id, cur.id, items), next_cursor=nxt)


def _folder(db: Session, workspace_id: str, folder_id: str | None):
    try:
        return org.owned_folder(db, workspace_id, folder_id)
    except org.OrganiseError as e:
        raise HTTPException(404, str(e)) from None


UPLOAD_DOC = multipart({
    "title": "Optional title (defaults to the file name)",
    "album_id": "Optional photo album to file it in",
    "on_duplicate": "skip (default) answers 200 with the existing item; keep stores it again",
}, file_help="An image, video or audio file. Large videos stream; nothing is buffered in memory")


@router.post("/media/upload", response_model=MediaItemOut, status_code=201, openapi_extra=UPLOAD_DOC,
             summary="Upload a file to the Library",
             description="multipart/form-data with one `file` part. 201 with the new item, or 200 with "
                         "`duplicate: true` when the same file is already in the Library.")
async def upload_media(request: Request, response: Response, db: Session = Depends(get_db),
                       cur: CurrentUser = Depends(require_editor)):
    from app.catalogue import importer

    folder, rel_folder = lib.media_dir(cur.workspace_id)
    got = await uploads.receive(request, folder)
    try:
        item, dup = await run_in_threadpool(importer.store, db, cur.workspace_id, cur.id, got, folder, rel_folder)
    except BaseException:
        got.path.unlink(missing_ok=True)
        raise
    out = await run_in_threadpool(lib.item_out, db, item)
    if dup:
        # already in the catalogue: nothing new was stored
        response.status_code = 200
        out.duplicate = True
    return out


@router.get("/media/{media_id}", response_model=MediaDetailOut)
def get_media_item(media_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    out = lib.detail(db, cur.workspace_id, media_id) if _UUID.match(media_id) else None
    if out is None:
        raise HTTPException(404, "Media not found")
    return org.annotate(db, cur.workspace_id, cur.id, [out])[0]


@router.patch("/media/{media_id}", response_model=MediaItemOut)
def patch_media(media_id: str, body: MediaPatch, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    item = _owned_item(db, media_id, cur.workspace_id)
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(422, "The title can't be blank")
        item.title = body.title.strip()
    if body.tags is not None:
        item.tags = lib.clean_tags(body.tags)
    lib.touch(item)
    db.commit()
    return lib.item_out(db, item)


@router.delete("/media/{media_id}", status_code=204)
def delete_media(media_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    item = _owned_item(db, media_id, cur.workspace_id)
    lib.delete_item(db, item)
    org.forget(db, cur.workspace_id, [media_id])
    db.commit()
    return Response(status_code=204)


@router.post("/media/{media_id}/regenerate", response_model=JobOut, status_code=202)
def regenerate_media(media_id: str, body: RegenerateIn, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    item = _owned_item(db, media_id, cur.workspace_id)
    if item.origin == "upload" or item.kind != "image":
        raise HTTPException(422, "Only generated images can be regenerated; use Edit or Upscale instead")
    # newest generated picture (not an upscale) is the recipe to re-roll
    base = db.scalars(select(Generation).where(
        Generation.target_type == "media", Generation.target_id == item.id, Generation.kind == "image",
    ).order_by(Generation.version.desc())).first()
    if base is None:
        raise HTTPException(409, "Nothing to regenerate yet")
    params = {k: v for k, v in (base.params or {}).items() if k not in ("comfy", "upscale", "size")}
    prompt, seed, note, rewrite_failed = base.prompt, new_seed(), None, None
    if body.mode == "note":
        if not (body.note and body.note.strip()):
            raise HTTPException(422, "A note is required for mode=note")
        note = body.note.strip()
        prompt, rewrite_failed = rewrite_prompt_from_note(base.prompt, note)
    elif body.mode == "edit":
        if body.prompt is not None:
            if not body.prompt.strip():
                raise HTTPException(422, "Prompt can't be empty")
            prompt = body.prompt.strip()
        if body.params:
            params.update({k: v for k, v in body.params.items() if k in ("negative", "steps", "seed")})
        if "seed" in params:
            seed = int(params.pop("seed"))
    params["created_by"] = {"user_id": cur.id, "flow": f"regenerate:{body.mode}"}
    g = enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="media",
                           target_id=item.id, kind="image", prompt=prompt, params=params, seed=seed,
                           parent_id=base.id, note=note)
    job = db.get(Job, g.job_id)
    if rewrite_failed:
        job.message = f"Waiting for a worker · note appended, AI rewrite unavailable ({rewrite_failed})"[:500]
    lib.touch(item)
    db.commit()
    return job_out(job)


# ---------------------------------------------------------------- image studio

def _new_image_item(db: Session, cur: CurrentUser, title: str, prompt: str, params: dict, seed: int | None,
                    folder_id: str | None = None, home: str | None = None):
    item = MediaItem(id=new_id(), workspace_id=cur.workspace_id, kind="image", origin="generated", title=title,
                     tags=[], width=params.get("width"), height=params.get("height"), folder_id=folder_id,
                     home=home)
    db.add(item)
    db.flush()
    g = enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="media",
                           target_id=item.id, kind="image", prompt=prompt, params=params, seed=seed)
    item.generation_id = g.id
    return item, db.get(Job, g.job_id)


@router.get("/images/options")
def image_options():
    return {"aspects": [{"id": a, "width": w, "height": h} for a, (w, h) in
                        ((a, lib.size_for_aspect(a)) for a in lib.ASPECTS)],
            **st.options(), "count": {"min": 1, "max": 4}, "edit_sources": {"min": 1, "max": 3}}


@router.get("/images/styles/{style_id}/preview")
def style_preview(style_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    s = st.catalogue().get(style_id)
    f = st.preview_file(s) if s is not None else None
    if f is None:
        raise HTTPException(404, "No preview for this style")
    release(db)
    return FileResponse(f, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


def _check_style(style: str | None) -> st.Style | None:
    if style and style not in lib.STYLES:
        raise HTTPException(422, f"Unknown style '{style}' (see GET /api/images/options for the {len(st.catalogue())} "
                                 "style ids)")
    return st.get(style)


@router.post("/images/generate", response_model=ImageBatchOut, status_code=202)
def generate_images(body: ImageGenerateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    style = _check_style(body.style)
    if not body.prompt.strip():
        raise HTTPException(422, "A prompt is required")
    into = generate_into(db, cur.workspace_id, body.folder_id, "image")
    model, chosen = rights.choose(db, body.model, "image", mn.plain(body.prompt), count=body.count)
    speed = _speed(model, body)
    magic = prompt_enhance.marker(body.magic_prompt, body.prompt_enhanced, "image",
                                  style=style.label if style else None,
                                  brand_kit_id=body.brand_kit_id)
    w, h = lib.size_for_aspect(body.aspect)
    # mentioned pictures turn the run into a reference-guided composition on the edit model
    editor = mc.default_for("edit")
    mentioned = mn.resolve(db, cur.workspace_id, body.prompt.strip(), budget=editor.max_refs or 3,
                           model_label=editor.label)
    prompt = lib.styled_prompt(mn.ref_preamble(mentioned.ref_labels) + mentioned.prompt, body.style)
    title = (body.title or "").strip() or lib.short_title(mn.plain(body.prompt))
    items, jobs = [], []
    for i in range(body.count):
        params = {"aspect": body.aspect, "width": w, "height": h, "style": body.style,
                  "user_prompt": mn.plain(body.prompt).strip(),
                  "created_by": {"user_id": cur.id, "flow": "image_generate"}, "magic_prompt": magic, **chosen}
        if mentioned.mentions:
            params["mentions"] = mentioned.mentions
        if mentioned.ref_ids:
            params["reference_ids"], params["reference_labels"] = list(mentioned.ref_ids), list(mentioned.ref_labels)
        negative = st.joined_negative(body.negative, style, speed.cfg if speed else None)
        if negative:
            params["negative"] = negative
        if body.steps:
            params["steps"] = body.steps
        if body.template_id:
            params["template_id"] = body.template_id
        params["model"] = model.id
        if speed:
            params["speed"] = speed.id
        seed = (body.seed + i) % 2**31 if body.seed is not None else None
        item, job = _new_image_item(db, cur, title, *brand.apply_to_request(
            db, cur.workspace_id, body.brand_kit_id, prompt, params), seed, into, lib.home_of(body.part_of))
        items.append(item)
        jobs.append(job)
    db.commit()
    return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs],
                         model_resolved=model.id, magic_prompt=magic)


def generate_into(db: Session, workspace_id: str, folder_id: str | None, kind: str) -> str | None:
    f = _folder(db, workspace_id, folder_id)
    if f is not None and f.kind not in ("any", kind):
        raise HTTPException(422, f"“{f.name}” only holds {f.kind}s")
    return f.id if f else None


def _speed(model: mc.Model, body) -> mc.Speed | None:
    # a speed picked for some other model shouldn't sink an auto pick that has no speeds
    if body.model == mc.AUTO and not model.speeds:
        return None
    return mc.speed_of(model, body.speed)


def _edit_source(db: Session, workspace_id: str, sid: str) -> tuple[str | None, Generation]:
    item = db.get(MediaItem, sid)
    if item is not None and item.workspace_id == workspace_id:
        g = db.get(Generation, item.generation_id) if item.generation_id else None
        media_id = item.id
    else:
        g = db.get(Generation, sid)
        if g is not None and (g.workspace_id != workspace_id or g.target_type.startswith("implant_")):
            g = None
        media_id = None
    if g is None:
        raise HTTPException(404, f"Source {sid} not found")
    if not (g.media_type or "").startswith("image/") or g.status not in lib.FINISHED or not g.file_path:
        raise HTTPException(422, f"Source {sid} isn't a finished image")
    return media_id, g


@router.get("/images/tools")
async def image_tools_list(cur: CurrentUser = Depends(get_current_user)):
    """Edit Image tools (contract v30) with live availability; switched-off ones are left out."""
    from app.config import get_settings
    from app.upscale import fetch_object_info

    driver = get_settings().gen_driver
    info, error = (None, None) if driver == "mock" else await fetch_object_info()
    return {"tools": it.options(info, driver=driver, error=error),
            "control": ctl.status(info, driver=driver, error=error)}


def _pick_tool(db: Session, body: ImageEditIn, template: dict | None,
               sources: list[tuple[str | None, Generation]]) -> tuple[it.ImageTool | None, str | None]:
    """(tool to run, None) or (None, why the edit model runs instead). 422 when nothing can run."""
    tool = None if body.engine == "edit" else it.tool_for(template, body.tool)
    if tool is None:
        if body.engine == "tool":
            raise HTTPException(422, "engine=tool needs a template with a tool, or tool")
        return None, None
    reason = it.unavailable(tool)
    if reason is None and tool.needs_mask and not body.mask and not it.has_selection(db, sources[0][1]):
        reason = "no mask: paint over what should go, or pick a Photo Studio selection"
    if reason is None:
        return tool, None
    if body.engine == "tool" or not tool.fallback:
        raise HTTPException(422, f"{tool.label} can't run: {reason}")
    return None, reason


def _tool_images(db: Session, cur: CurrentUser, body: ImageEditIn, tool: it.ImageTool, template: dict | None,
                 sources: list[tuple[str | None, Generation]], into: str | None) -> ImageBatchOut:
    try:
        rec = it.build_params(db, cur.workspace_id, tool, template, body.instruction, [g for _, g in sources],
                              body.tool_options, body.mask)
    except it.ToolError as e:
        raise HTTPException(422, str(e)) from None
    ran = it.TOOLS[rec["tool"]]
    w, h = rec["inputs"]["width"], rec["inputs"]["height"]
    text = mn.plain(body.instruction).strip()
    title = (body.title or "").strip() or (f"{ran.label} · {lib.short_title(text, 5)}" if text else ran.label)
    edit_of = [{"media_id": m, "generation_id": g.id} for m, g in sources]
    # line art, depth, edges and LaMa give the same picture every time: one is enough
    count = body.count if ran.generative else 1
    items, jobs = [], []
    for i in range(count):
        params = {"width": w, "height": h, "aspect": body.aspect, "image_tool": rec, "edit_of": edit_of,
                  "instruction": text or None, "created_by": {"user_id": cur.id, "flow": "image_tool"}}
        if body.template_id:
            params["template_id"] = body.template_id
        seed = (body.seed + i) % 2**31 if body.seed is not None else None
        item, job = _new_image_item(db, cur, title, text or ran.label, params, seed, into)
        items.append(item)
        jobs.append(job)
    db.commit()
    return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs], tool_resolved=ran.id)


@router.post("/images/edit", response_model=ImageBatchOut, status_code=202)
def edit_images(body: ImageEditIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    template = templates().get(body.template_id) if body.template_id else None
    if body.template_id and (template is None or template.get("target") != "image-edit"):
        raise HTTPException(422, f"'{body.template_id}' isn't an Edit Image template")
    if template is not None and not it.template_visible(template):
        raise HTTPException(422, f"“{template['title']}” isn't available on this server.")
    into = generate_into(db, cur.workspace_id, body.folder_id, "image")
    sources = [_edit_source(db, cur.workspace_id, sid) for sid in body.source_ids[:3]]
    tool, fallback = _pick_tool(db, body, template, sources)
    if tool is not None:
        return _tool_images(db, cur, body, tool, template, sources, into)
    if not body.instruction.strip():
        raise HTTPException(422, "Say what to change")
    model, chosen = rights.choose(db, body.model, "edit", mn.plain(body.instruction), refs=len(body.source_ids))
    mc.check_refs(model, len(body.source_ids))
    mentioned = mn.resolve(db, cur.workspace_id, body.instruction.strip(), budget=model.max_refs or 3,
                           used=len(sources), model_label=model.label)
    if body.aspect:
        w, h = lib.size_for_aspect(body.aspect)
    else:
        # keep the first picture's shape at ~1 MP
        sw, sh = lib._image_dims(sources[0][1])
        w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
    instruction = mn.ref_preamble(mentioned.ref_labels, len(sources) + 1) + mentioned.prompt
    magic = prompt_enhance.marker(body.magic_prompt or "off", body.prompt_enhanced, "edit",
                                  brand_kit_id=body.brand_kit_id)
    title = (body.title or "").strip() or f"Edit · {lib.short_title(mn.plain(body.instruction).strip(), 5)}"
    edit_of = [{"media_id": m, "generation_id": g.id} for m, g in sources]
    items, jobs = [], []
    for i in range(body.count):
        params = {"width": w, "height": h, "aspect": body.aspect, "instruction": instruction, "edit_of": edit_of,
                  "reference_ids": [g.id for _, g in sources] + mentioned.ref_ids, "model": model.id,
                  "created_by": {"user_id": cur.id, "flow": "image_edit"}, "magic_prompt": magic,
                  "user_prompt": mentioned.prompt.strip(), **chosen}
        if mentioned.mentions:
            params["mentions"] = mentioned.mentions
        if body.template_id:
            params["template_id"] = body.template_id
        if fallback:
            params["tool_fallback"] = fallback
        seed = (body.seed + i) % 2**31 if body.seed is not None else None
        item, job = _new_image_item(db, cur, title, *brand.apply_to_request(
            db, cur.workspace_id, body.brand_kit_id, instruction, params, edit=True, max_refs=model.max_refs or 3), seed,
            into)
        items.append(item)
        jobs.append(job)
    db.commit()
    return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs], model_resolved=model.id,
                         tool_fallback=fallback)


@router.post("/images/img2img", response_model=ImageBatchOut, status_code=202)
def img2img(body: Img2ImgIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    style = _check_style(body.style)
    if not body.prompt.strip() and style is None:
        raise HTTPException(422, "Describe the picture you want, or pick a style")
    # the slider starts where the style's group works best; a value the user set always wins
    strength = body.strength
    if "strength" not in body.model_fields_set and style is not None:
        strength = st.restyle_strength(style) or strength
    into = generate_into(db, cur.workspace_id, body.folder_id, "image")
    model, chosen = rights.choose(db, body.model, "image", body.prompt or style.positive, count=body.count, cap="i2i")
    reason = mc.capability_unavailable(model, "i2i")
    if reason:
        raise HTTPException(422, f"Image to image with {model.label} isn't available: {reason}")
    speed = _speed(model, body)
    # nothing of the user's to improve when the style is the whole prompt
    magic = prompt_enhance.marker(body.magic_prompt if body.prompt.strip() else "off", body.prompt_enhanced, "image",
                                  style=style.label if style else None, brand_kit_id=body.brand_kit_id)
    media_id, src = _edit_source(db, cur.workspace_id, body.source_id)
    # the redraw graph has one image input, so mentions stay words
    mentioned = mn.resolve(db, cur.workspace_id, body.prompt.strip(), budget=0) if body.prompt.strip() else None
    if body.aspect == "source":
        sw, sh = lib._image_dims(src)
        w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
    else:
        w, h = lib.size_for_aspect(body.aspect)
    prompt = lib.styled_prompt(mentioned.prompt if mentioned else "", body.style)
    said = mn.plain(body.prompt).strip()
    title = (body.title or "").strip() or f"Restyle · {lib.short_title(said, 5) if said else style.label}"
    negative = st.joined_negative(body.negative, style, speed.cfg if speed else None)
    items, jobs = [], []
    for i in range(body.count):
        params = {"width": w, "height": h, "aspect": body.aspect, "model": model.id,
                  "user_prompt": said, "style": body.style,
                  "img2img_of": {"media_id": media_id, "generation_id": src.id, "strength": strength},
                  "created_by": {"user_id": cur.id, "flow": "image_img2img"}, "magic_prompt": magic, **chosen}
        if speed:
            params["speed"] = speed.id
        if negative:
            params["negative"] = negative
        if mentioned and mentioned.mentions:
            params["mentions"] = mentioned.mentions
        if body.template_id:
            params["template_id"] = body.template_id
        seed = (body.seed + i) % 2**31 if body.seed is not None else None
        item, job = _new_image_item(db, cur, title, *brand.apply_to_request(
            db, cur.workspace_id, body.brand_kit_id, prompt, params), seed, into)
        items.append(item)
        jobs.append(job)
    db.commit()
    return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs],
                         model_resolved=model.id, magic_prompt=magic)


@router.post("/images/control", response_model=ImageBatchOut, status_code=202)
def image_control(body: ImageControlIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Depth / Sketch / Pose / Edge to image (contract v31)."""
    c = ctl.TYPES[body.control]
    style = _check_style(body.style)
    if not body.prompt.strip():
        raise HTTPException(422, "Describe the picture you want")
    template = templates().get(body.template_id) if body.template_id else None
    into = generate_into(db, cur.workspace_id, body.folder_id, "image")
    media_id, src = _edit_source(db, cur.workspace_id, body.source_id)
    if body.aspect == "source":
        sw, sh = lib._image_dims(src)
        w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
    else:
        w, h = lib.size_for_aspect(body.aspect)

    reason = None if body.engine == "edit" else ctl.unavailable(c, body.use_as_is)
    if body.engine == "edit":
        reason = "the edit model was asked for"
    elif reason and body.engine == "controlnet":
        raise HTTPException(422, f"{c.label} to image can't run on the ControlNet: {reason}")

    said = mn.plain(body.prompt).strip()
    title = (body.title or "").strip() or f"{c.label} · {lib.short_title(said, 5)}"
    control_of = {"media_id": media_id, "generation_id": src.id, "type": c.id, "as_is": body.use_as_is}
    items, jobs = [], []
    if reason is None:
        try:
            rec = ctl.build_record(c, src, cur.workspace_id, as_is=body.use_as_is, strength=body.strength,
                                   size=(w, h))
        except ctl.ControlError as e:
            raise HTTPException(422, str(e)) from None
        prompt = lib.styled_prompt(said, body.style)
        for i in range(body.count):
            params = {"width": w, "height": h, "aspect": body.aspect, "control": rec, "control_of": control_of,
                      "user_prompt": said, "style": body.style, "model": "zimage_turbo",
                      "created_by": {"user_id": cur.id, "flow": "image_control"}}
            if body.negative:
                params["negative"] = body.negative.strip()
            if template is not None:
                params["template_id"] = template["id"]
            seed = (body.seed + i) % 2**31 if body.seed is not None else None
            item, job = _new_image_item(db, cur, title, prompt, params, seed, into)
            items.append(item)
            jobs.append(job)
        db.commit()
        return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs],
                             model_resolved="zimage_turbo", tool_resolved=f"controlnet:{c.id}")

    # Qwen-Image-Edit reads the picture as a reference: it follows the layout, less exactly
    editor = mc.default_for("edit")
    instruction = ctl.fallback_instruction(c, said, style.positive if style else None)
    for i in range(body.count):
        params = {"width": w, "height": h, "aspect": body.aspect, "instruction": instruction,
                  "edit_of": [{"media_id": media_id, "generation_id": src.id}], "reference_ids": [src.id],
                  "model": editor.id, "control_of": control_of, "tool_fallback": reason, "style": body.style,
                  "created_by": {"user_id": cur.id, "flow": "image_control"}}
        if template is not None:
            params["template_id"] = template["id"]
        seed = (body.seed + i) % 2**31 if body.seed is not None else None
        item, job = _new_image_item(db, cur, title, instruction, params, seed, into)
        items.append(item)
        jobs.append(job)
    db.commit()
    return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs], model_resolved=editor.id,
                         tool_fallback=reason)


def _brand_slots(text: str, what: str) -> None:
    left = placeholders(text)
    if left:
        raise HTTPException(422, f"Replace {', '.join(f'[{x}]' for x in left)} in the {what} first")


@router.post("/images/brand", response_model=ImageBatchOut, status_code=202)
def brand_images(body: BrandImageIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Brand templates (contract v35): the user's photo, or a made-up scene, with the kit's logo put on it."""
    t = templates().get(body.template_id)
    if not bt.is_brand(t):
        raise HTTPException(422, f"'{body.template_id}' isn't a Brand template")
    kit = bt.pick_kit(db, cur.workspace_id, body.brand_kit_id)
    surface = bt.surface_of(t, body.surface)
    logo = bt.pick_logo(db, kit, surface)
    product = bt.pick_product(db, kit, body.product_id, "product" in (t.get("brand_refs") or []))
    values = bt.tokens(kit, logo, product, t)
    d = t["defaults"]
    instruction = bt.fill(body.instruction or d["instruction_scaffold"], values)
    _brand_slots(instruction, "logo wording")
    into = generate_into(db, cur.workspace_id, body.folder_id, "image")
    refs = [logo.generation_id] + ([product[1]] if product else [])
    model, chosen = rights.choose(db, body.model or d.get("model"), "edit", instruction, refs=1 + len(refs))
    mc.check_refs(model, 1 + len(refs))
    record = {"kit_id": kit.id, "kit_name": kit.name, "logo_variant": logo.variant, "surface": surface,
              "mode": body.mode, "product_id": product[0]["media_id"] if product else None}
    title = (body.title or "").strip() or f"{t['title']} · {kit.name}"
    items, jobs = [], []

    if body.mode == "photo":
        if not body.source_id:
            raise HTTPException(422, "Add your photo first")
        media_id, src = _edit_source(db, cur.workspace_id, body.source_id)
        if body.aspect:
            w, h = lib.size_for_aspect(body.aspect)
        else:
            sw, sh = lib._image_dims(src)
            w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
        for i in range(body.count):
            params = {"width": w, "height": h, "aspect": body.aspect, "instruction": instruction,
                      "edit_of": [{"media_id": media_id, "generation_id": src.id}],
                      "reference_ids": [src.id, *refs], "model": model.id, "template_id": t["id"],
                      "brand": {"kit_id": kit.id}, "brand_template": record,
                      "created_by": {"user_id": cur.id, "flow": "brand_template:photo"}, **chosen}
            seed = (body.seed + i) % 2**31 if body.seed is not None else None
            item, job = _new_image_item(db, cur, title, instruction, params, seed, into)
            items.append(item)
            jobs.append(job)
        db.commit()
        return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs],
                             model_resolved=model.id, brand_template=record)

    # scene mode: v1 is the scene with a blank surface, v2 (queued when v1 lands) carries the logo
    scene = t.get("scene") or {}
    prompt = bt.fill(body.scene_prompt or scene.get("prompt_scaffold") or "", values)
    if not prompt:
        raise HTTPException(422, "This card has no scene to make; use your own photo")
    _brand_slots(prompt, "scene")
    aspect = body.aspect or scene.get("aspect") or d.get("aspect") or "1:1"
    w, h = lib.size_for_aspect(aspect)
    smodel, schosen = rights.choose(db, scene.get("model") or mc.AUTO, "image", prompt, count=body.count)
    speed = mc.speed_of(smodel, None) if smodel.speeds else None
    negative = st.joined_negative(scene.get("negative"), None, speed.cfg if speed else None)
    for i in range(body.count):
        seed = (body.seed + i) % 2**31 if body.seed is not None else None
        spec = bt.chain_spec(instruction=instruction, model_id=model.id, chosen=chosen, refs=refs, size=(w, h),
                             aspect=aspect, seed=seed, template_id=t["id"], record=record, user_id=cur.id)
        params = {"aspect": aspect, "width": w, "height": h, "style": None, "user_prompt": prompt,
                  "model": smodel.id, "template_id": t["id"], "brand_template": record, bt.CHAIN: spec,
                  "created_by": {"user_id": cur.id, "flow": "brand_template:scene"}, **schosen}
        if speed:
            params["speed"] = speed.id
        if negative:
            params["negative"] = negative
        item, job = _new_image_item(db, cur, title, prompt, params, seed, into)
        items.append(item)
        jobs.append(job)
    db.commit()
    return ImageBatchOut(items=lib.items_out(db, items), jobs=[job_out(j) for j in jobs], model_resolved=model.id,
                         brand_template=record)


# ---------------------------------------------------------------- prompt templates
# Shown to users as "Prompt templates"; /templates stays as the old name for the same thing.

PREVIEW_DIR = TEMPLATE_DIR / "previews"


MINE = "My templates"


@lru_cache
def categories() -> dict[str, list[str]]:
    return {kind: json.loads((TEMPLATE_DIR / f"{kind}.json").read_text(encoding="utf-8")).get("categories", [])
            for kind in ("video", "image", "audio")}


def style_options() -> list[dict]:
    """The "Restyle" card's picker: one choice per catalogue style, written into its [style] slot."""
    return [{"slot": "style", "label": "Style", "gallery": "styles",
             "choices": [{"label": s.label, "value": f"{s.label}: {s.positive}", "style_id": s.id}
                         for s in st.catalogue().values()]}]


@lru_cache
def templates() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for kind in ("video", "image", "audio"):
        data = json.loads((TEMPLATE_DIR / f"{kind}.json").read_text(encoding="utf-8"))
        for t in data["templates"]:
            row = {**t, "type": kind}
            if kind == "image":
                row.setdefault("target", "image-generate")  # v2 cards predate edit templates
            if t.get("options_from") == "styles":
                row["options"] = style_options()
            out[t["id"]] = row
    return out


def preview_file(template_id: str) -> Path | None:
    if template_id not in templates():
        return None
    root = PREVIEW_DIR.resolve()
    target = (root / f"{template_id}.webp").resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def control_card(t: dict) -> dict | None:
    c = ctl.TYPES.get(t.get("control") or "")
    if c is None:
        return None
    return {"type": c.id, "label": c.label, "detector": c.detector, "licence": c.licence,
            "noncommercial": c.noncommercial, "as_is": bool((t.get("defaults") or {}).get("use_as_is"))}


def _template_out(t: dict, preview_url: str | None = None) -> TemplateOut:
    f = preview_file(t["id"])
    # mtime in the URL so a re-rendered preview isn't stuck behind the cache
    url = f"/api/prompt-templates/{t['id']}/preview?v={int(f.stat().st_mtime)}" if f else preview_url
    return TemplateOut(**{k: t.get(k) for k in ("id", "type", "title", "description", "thumb", "defaults", "category",
                                                "target")},
                       tags=t.get("tags") or [], examples=t.get("examples") or {}, preview_url=url,
                       requires_brand=bool(t.get("requires_brand")), refs_required=t.get("refs_required") or 0,
                       ref_labels=t.get("ref_labels") or [], options=t.get("options") or [],
                       tool=it.template_tool(t), control=control_card(t), mine=bool(t.get("mine")),
                       brand_refs=t.get("brand_refs") or [], surface=t.get("surface"), scene=t.get("scene"))


# ---------------------------------------------------------------- my templates (v31)

def _mine_dict(row: UserTemplate) -> dict:
    edit = row.target == "edit"
    d: dict = {"count": row.count, "aspect": row.aspect}
    if row.model:
        d["model"] = row.model
    if edit:
        d["instruction_scaffold"] = row.prompt
    else:
        d.update(prompt_scaffold=row.prompt, aspect=row.aspect or "1:1", style=row.style,
                 negative=row.negative or None)
    return {"id": row.id, "type": "image", "title": row.title, "description": row.description or "",
            "category": MINE, "target": "image-edit" if edit else "image-generate", "defaults": d, "tags": [],
            "examples": dict(row.examples or {}),
            "ref_labels": (list(row.ref_labels or []) or ["Photo"]) if edit else [],
            "refs_required": 1 if edit else 0, "mine": True}


def _mine_out(db: Session, rows: list[UserTemplate]) -> list[TemplateOut]:
    from app import thumbs

    ids = [r.preview_generation_id for r in rows if r.preview_generation_id]
    gens = {g.id: g for g in db.scalars(select(Generation).where(Generation.id.in_(ids)))} if ids else {}
    out = []
    for r in rows:
        g = gens.get(r.preview_generation_id or "")
        url = thumbs.url_for(g) if g is not None and g.workspace_id == r.workspace_id else None
        out.append(_template_out(_mine_dict(r), url))
    return out


def _my_rows(db: Session, workspace_id: str, want: bool | None = None) -> list[UserTemplate]:
    return list(db.scalars(select(UserTemplate).where(UserTemplate.workspace_id == workspace_id,
                                                      *vis.where(UserTemplate, want))
                           .order_by(UserTemplate.updated_at.desc(), UserTemplate.id)))


def _my_template(db: Session, workspace_id: str, template_id: str) -> UserTemplate:
    row = db.get(UserTemplate, template_id) if _UUID.match(template_id) else None
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(404, "Template not found")
    return row


def _check_mine(body: UserTemplateIn) -> None:
    kind = "edit" if body.target == "edit" else "image"
    if body.target == "edit":
        if body.style or body.negative.strip():
            raise HTTPException(422, "Edit templates have no style or negative prompt")
    else:
        _check_style(body.style)
        if body.ref_labels:
            raise HTTPException(422, "Only edit templates name their pictures")
    if body.model and body.model != mc.AUTO:
        m = mc.MODELS.get(body.model)
        if m is None or m.type != kind:
            raise HTTPException(422, f"Unknown {kind} model '{body.model}'")
    if any(not label.strip() for label in body.ref_labels):
        raise HTTPException(422, "Picture labels can't be blank")


@router.get("/my-templates", response_model=list[TemplateOut])
def list_my_templates(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return _mine_out(db, _my_rows(db, cur.workspace_id, vis.wanted(cur)))


@router.post("/my-templates", response_model=TemplateOut, status_code=201)
def save_my_template(body: UserTemplateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    _check_mine(body)
    preview = None
    if body.from_media_id:
        _, g = _edit_source(db, cur.workspace_id, body.from_media_id)
        preview = g.id
    slots = set(placeholders(body.prompt))
    row = UserTemplate(id=new_id(), workspace_id=cur.workspace_id, created_by=cur.id, title=body.title.strip(),
                       description=body.description.strip(), target=body.target, prompt=body.prompt.strip(),
                       negative=body.negative.strip(), style=body.style or None, model=body.model or None,
                       aspect=body.aspect, count=body.count, ref_labels=[x.strip()[:40] for x in body.ref_labels],
                       examples={k: v[:300] for k, v in body.examples.items() if k in slots},
                       preview_generation_id=preview)
    db.add(row)
    db.commit()
    return _mine_out(db, [row])[0]


@router.patch("/my-templates/{template_id}", response_model=TemplateOut)
def patch_my_template(template_id: str, body: UserTemplatePatch, db: Session = Depends(get_db),
                      cur: CurrentUser = Depends(require_editor)):
    row = _my_template(db, cur.workspace_id, template_id)
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(422, "The name can't be blank")
        row.title = body.title.strip()
    if body.description is not None:
        row.description = body.description.strip()
    if body.prompt is not None:
        if not body.prompt.strip():
            raise HTTPException(422, "The prompt can't be blank")
        row.prompt = body.prompt.strip()
    row.updated_at = utcnow()
    db.commit()
    return _mine_out(db, [row])[0]


@router.delete("/my-templates/{template_id}", status_code=204)
def delete_my_template(template_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    db.delete(_my_template(db, cur.workspace_id, template_id))
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- listing and starting

@router.get("/templates", response_model=list[TemplateOut])
@router.get("/prompt-templates", response_model=list[TemplateOut])
def list_templates(type: Literal["video", "image", "audio"] | None = None,
                   category: str | None = Query(None, max_length=80), db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(get_current_user)):
    rows = [t for t in templates().values() if type in (None, t["type"]) and it.template_visible(t)
            and category in (None, t.get("category"))]
    order = categories()

    def rank(t: dict) -> int:
        cats = order.get(t["type"], [])
        return cats.index(t["category"]) if t.get("category") in cats else len(cats)

    kinds = ("video", "image", "audio")
    shipped = [_template_out(t) for t in sorted(rows, key=lambda t: (kinds.index(t["type"]), rank(t)))]
    if type not in (None, "image") or category not in (None, MINE):
        return shipped
    # the workspace's own lead the image cards (mine=true, category "My templates")
    mine = _mine_out(db, _my_rows(db, cur.workspace_id, vis.wanted(cur)))
    at = next((i for i, t in enumerate(shipped) if t.type == "image"), len(shipped))
    return shipped[:at] + mine + shipped[at:]


@router.get("/templates/{template_id}/preview")
@router.get("/prompt-templates/{template_id}/preview")
def template_preview(template_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    f = preview_file(template_id)
    if f is None:
        raise HTTPException(404, "No preview for this prompt template")
    release(db)
    return FileResponse(f, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


def placeholders(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\[([^\[\]]{1,60})\]", text)))


@router.post("/templates/{template_id}/start", response_model=TemplateStartOut)
@router.post("/prompt-templates/{template_id}/start", response_model=TemplateStartOut)
def start_template(template_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    t = templates().get(template_id)
    if t is None and _UUID.match(template_id):
        row = db.get(UserTemplate, template_id)
        t = _mine_dict(row) if row is not None and row.workspace_id == cur.workspace_id else None
    if t is None or not it.template_visible(t):
        raise HTTPException(404, "Prompt template not found")
    d = dict(t["defaults"])
    scaffold = d.pop("prompt_scaffold", "")
    if t.get("target") == "image-edit":
        # opens Edit Image: the user still has to add the labelled pictures before anything runs
        scaffold = d.get("instruction_scaffold", "")
        prefill = {"instruction": scaffold, "model": d.get("model"), "count": d.get("count", 1),
                   "aspect": d.get("aspect"), "refs_required": t.get("refs_required", 1),
                   "ref_labels": t.get("ref_labels") or [], "options": t.get("options") or [], "template_id": t["id"]}
        if t.get("tool"):
            prefill["tool"] = it.template_tool(t)
        target = "image-edit"
    elif bt.is_brand(t):
        # opens the Brand page: the kit fills the logo (and product) slots; the user adds a photo, or nothing
        scene = t.get("scene") or {}
        prefill = {"instruction": d.get("instruction_scaffold", ""), "model": d.get("model"),
                   "count": d.get("count", 1), "aspect": d.get("aspect"), "ref_labels": t.get("ref_labels") or [],
                   "options": t.get("options") or [], "brand_refs": t.get("brand_refs") or ["logo"],
                   "surface": bt.surface_of(t, None), "scene_prompt": scene.get("prompt_scaffold", ""),
                   "scene_aspect": scene.get("aspect") or d.get("aspect") or "1:1", "template_id": t["id"]}
        scaffold = f"{prefill['scene_prompt']} {prefill['instruction']}"
        target = "image-brand"
    elif t.get("target") == "image-control":
        # opens Control to image: the photo, map or sketch is still to come
        prefill = {"prompt": scaffold, "control": t["control"], "use_as_is": bool(d.get("use_as_is")),
                   "strength": d.get("strength", ctl.STRENGTH_DEFAULT), "count": d.get("count", 1),
                   "aspect": d.get("aspect", "source"), "style": d.get("style"), "template_id": t["id"],
                   "control_card": control_card(t)}
        target = "image-control"
    elif t["type"] == "image":
        prefill = {"prompt": scaffold, "aspect": d.get("aspect", "1:1"), "count": d.get("count", 1),
                   "style": d.get("style"), "negative": d.get("negative"), "template_id": t["id"]}
        if d.get("model"):
            prefill["model"] = d["model"]
        target = "image"
    elif t["type"] == "audio":
        prefill = {k: d.get(k) for k in ("kind", "lyrics", "language", "vocal", "bpm", "category", "duration_s")
                   if d.get(k) is not None}
        prefill.update(prompt=scaffold, template_id=t["id"])
        target = "audio"
    elif d.get("authoring_mode"):
        prefill = {"title": "", "authoring_mode": d["authoring_mode"], "logline": "",
                   "logline_hint": d.get("logline_hint", scaffold), "target_runtime_s": d.get("target_runtime_s"),
                   "aspect_ratio": d.get("aspect_ratio", "16:9"), "brief": scaffold, "template_id": t["id"]}
        target = "studio"
    else:
        prefill = {"prompt": scaffold, "duration_s": d.get("duration_s"), "aspect_ratio": d.get("aspect_ratio"),
                   "style": d.get("style"), "dialogue": d.get("dialogue"), "template_id": t["id"]}
        target = "quick"
    prefill["placeholders"] = placeholders(scaffold)
    if t.get("examples"):
        prefill["examples"] = t["examples"]
    if t.get("requires_brand"):
        prefill["requires_brand"] = True
    if t.get("mine"):
        prefill["mine"] = True
    return TemplateStartOut(target=target, prefill=prefill)


# ---------------------------------------------------------------- dashboard

@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    from app.api.quick import recent

    ws, want = cur.workspace_id, vis.wanted(cur)
    projects = db.scalars(select(Project).where(Project.workspace_id == ws, Project.authoring_mode != "podcast",
                                                *vis.where(Project, want))
                          .order_by(Project.updated_at.desc()).limit(6)).all()
    videos, _ = lib.list_media(db, ws, kind="video", include_project=True, limit=6, want=want)
    images, _ = lib.list_media(db, ws, kind="image", limit=8, want=want)
    org.annotate(db, ws, cur.id, videos + images)
    running = db.scalars(select(Job).where(Job.workspace_id == ws, Job.status.in_(("queued", "running")),
                                           *vis.where(Job, want))
                         .order_by(Job.created_at.desc()).limit(20)).all()
    quick = [r.model_dump(mode="json") for r in recent(db, cur)[:4]]
    return DashboardOut(recent_projects=[project_out(db, p) for p in projects], recent_videos=videos,
                        recent_images=images, running_jobs=[job_out(j) for j in running], quick_recent=quick)


# ---------------------------------------------------------------- upscales (P5)

@router.get("/upscales", response_model=UpscalePage)
def list_upscales(kind: Literal["image", "video"] | None = None, limit: int = Query(20, ge=1, le=lib.MAX_LIMIT),
                  cursor: str | None = None, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(get_current_user)):
    rows, nxt = lib.list_upscales(db, cur.workspace_id, kind=kind, limit=limit, cursor=cursor, want=vis.wanted(cur))
    return UpscalePage(items=rows, next_cursor=nxt)


@router.delete("/upscales/{gen_id}", status_code=204)
def delete_upscale(gen_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    g = db.get(Generation, gen_id)
    if g is None or g.workspace_id != cur.workspace_id or not isinstance((g.params or {}).get("upscale"), dict):
        raise HTTPException(404, "Upscale not found")
    if g.status == "approved":
        raise HTTPException(409, "This upscale is the approved version in its project; pick another there first")
    lib.delete_upscale(db, g)
    db.commit()
    return Response(status_code=204)
