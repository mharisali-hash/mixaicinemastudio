"""3D Studio (contract v40): POST /3d/make turns a picture, a prompt, a character, a few views of one object or a
logo into a textured GLB in the Library. The turntable and thumbnail routes take what the browser's viewer made."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import brand, prompt_enhance, thumbs, uploads
from app import library as lib
from app import model3d as m3
from app import model3d_logo as ml
from app import models_catalog as mc
from app import rights
from app.api.library import _edit_source, _new_image_item, generate_into
from app.config import get_settings
from app.db import get_db
from app.models import Character, Generation, Job, MediaItem, new_id
from app.openapi import multipart
from app.schemas import MediaItemOut, Model3DIn, Model3DLogo, Model3DOut, Shots3DOut, Turntable3DOut
from app.security import CurrentUser, get_current_user, require_editor, require_feature
from app.services import READY_STATES, enqueue_generation, get_owned, job_out, new_seed, next_version

router = APIRouter(tags=["3d"], dependencies=[Depends(require_feature("3d"))])

NO_ALPHA = "The picture has no transparent background, so the background was removed anyway."
NO_ALPHA_VIEWS = "Not every view has a transparent background, so the backgrounds were removed anyway."
VIEWS_PIXAL = "Several views only work with Pixal3D, so Pixal3D made this one."
BACK_PIXAL = "Better back works with Pixal3D only, so Pixal3D made this one."
BACK_BACKGROUND = "The side and back views are made on a plain background, so the backgrounds were removed."


@router.get("/3d/options")
def options(cur: CurrentUser = Depends(get_current_user)):
    pixal = mc.MODELS["pixal3d"]
    why = mc.capability_unavailable(pixal, "multiview")
    return {
        "engines": [{"id": m.id, "label": m.label, "description": m.description, "default": m.default,
                     "best_for": list(m.best_for)} for m in mc.MODELS.values() if m.type == "model3d"],
        "qualities": [{"id": q, "label": m3.QUALITY_LABELS[q]} for q in m3.QUALITY],
        "default_quality": "final",
        "views": {"ids": list(m3.VIEWS), "required": ["front"], "min": 2, "engine": "pixal3d",
                  "available": why is None, "reason": why},
        "better_back": {"available": why is None, "reason": why, "engine": "pixal3d"},
        "logo": {"depths": list(ml.DEPTHS), "bevels": list(ml.BEVELS),
                 "finishes": [{"id": f, "label": ml.FINISH_LABELS[f]} for f in ml.FINISHES],
                 "backs": list(ml.BACKS), "defaults": dict(ml.DEFAULTS)},
    }


def _see_through(paths, wanted: bool) -> bool:
    # without an alpha channel the template's "keep" branch crops to an empty mask
    return wanted or not all(m3.has_transparency(p) for p in paths)


def _picked_views(db: Session, workspace_id: str, body: Model3DIn) -> dict[str, tuple[str | None, Generation]]:
    asked = {v: getattr(body.views, v) for v in m3.VIEWS if getattr(body.views, v)}
    out = {v: _edit_source(db, workspace_id, sid) for v, sid in asked.items()}
    gens = [g.id for _, g in out.values()]
    if len(set(gens)) != len(gens):
        raise HTTPException(422, "Each view needs its own picture")
    if len(out) < 2:
        raise HTTPException(422, "Add at least one more view (left, back or right), or start from one picture")
    return out


@router.post("/3d/make", response_model=Model3DOut, status_code=202,
             summary="Make a 3D model",
             description="Queues one job on the image GPU (two for a text start: the picture, then the model). The "
                         "result is a Library item of kind `model` whose file is a binary glTF "
                         "(`model/gltf-binary`); list them with `GET /api/media?kind=model`.")
def make_3d(body: Model3DIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ws = cur.workspace_id
    into = generate_into(db, ws, body.folder_id, m3.MEDIA_KIND)
    if body.logo_id:
        return _make_logo(db, cur, body, into)
    root = get_settings().data_dir
    rec: dict = {"quality": body.quality}
    extra: dict = {}
    out: dict = {"start": "picture"}
    views: dict[str, tuple[str | None, Generation]] | None = None
    title = (body.title or "").strip()
    prompt = ""

    if body.views:
        if body.engine == "trellis2":
            raise HTTPException(422, "TRELLIS.2 takes one picture. Use Pixal3D for several views.")
        views = _picked_views(db, ws, body)
        out["start"] = "views"
        rec["measure_fov"] = True  # most people's views are photos
        front = db.get(MediaItem, views["front"][0]) if views["front"][0] else None
        title = title or (f"{front.title} · 3D" if front and front.title else "3D model")
    elif body.character_id:
        c = get_owned(db, Character, body.character_id, ws, "Character")
        turn, single = m3.character_views(db, c.id)
        if turn is None and single is None:
            raise HTTPException(422, f"{c.name} has no picture yet. Make the portrait first.")
        out["start"] = "character"
        extra["character_id"] = c.id
        rec["character_id"] = c.id
        title = title or f"{c.name} · 3D"
        if turn is not None:
            views = {v: (None, g) for v, g in turn.items()}
            rec["measure_fov"] = False  # turnaround panels are framed like a render, not a photo
        else:
            rec.update(source_id=single.id, source_media_id=None)
            prompt = single.prompt or ""
    elif body.prompt:
        out["start"] = "text"
        prompt = m3.object_prompt(body.prompt)
        title = title or f"{lib.short_title(body.prompt)} · 3D"
    else:
        media_id, src = _edit_source(db, ws, body.source_id)
        source_item = db.get(MediaItem, media_id) if media_id else None
        title = title or (f"{source_item.title} · 3D" if source_item and source_item.title else "3D model")
        rec.update(source_id=src.id, source_media_id=media_id)
        prompt = src.prompt or ""

    # better back: on by default only where a character has no turnaround to use
    want = body.better_back if body.better_back is not None else out["start"] == "character"
    better = bool(want) and views is None and out["start"] in ("picture", "character")
    edit = None
    if better:
        why = mc.capability_unavailable(mc.MODELS["pixal3d"], "multiview")
        try:
            edit = None if why else mc.choose(m3.BACK_EDIT_MODEL, "edit")
        except HTTPException as e:
            if e.status_code != 422:
                raise
            why = str(e.detail)
        if why:
            better = False
            out["better_back_note"] = f"Better back isn't available right now ({why}), so the one picture was used."

    engine_id = body.engine
    if views is not None or better:
        if engine_id != "pixal3d":
            out["engine_note"] = VIEWS_PIXAL if views is not None else BACK_PIXAL
        engine_id = "pixal3d"
    if views is not None:
        why = mc.capability_unavailable(mc.MODELS["pixal3d"], "multiview")
        if why:
            raise HTTPException(422, f"Pixal3D multi-view isn't available right now: {why}")
    engine, chosen = rights.choose(db, engine_id, "model3d")

    remove_bg = body.remove_background
    if views is not None:
        remove_bg = _see_through([root / g.file_path for _, g in views.values()], remove_bg)
        if remove_bg and not body.remove_background:
            out["background_note"] = NO_ALPHA_VIEWS
        rec.update(views={v: g.id for v, (_, g) in views.items()},
                   view_media={v: mid for v, (mid, _) in views.items() if mid},
                   source_id=views["front"][1].id, source_media_id=views["front"][0])
        out["views_used"] = list(views)
        prompt = prompt or views["front"][1].prompt or ""
    elif out["start"] != "text":
        src = db.get(Generation, rec["source_id"])
        remove_bg = _see_through([root / src.file_path], remove_bg)
        if remove_bg and not body.remove_background:
            out["background_note"] = NO_ALPHA
    # a text start's picture is made on a plain background, so it always needs cutting out
    rec = {"engine": engine.id, **rec, "remove_background": remove_bg or out["start"] == "text"}
    if better:
        if not body.remove_background:
            out["background_note"] = BACK_BACKGROUND
        # the views made from it have no alpha; a photo's field of view is measured, a character's is a render's
        rec.update(remove_background=True, measure_fov=out["start"] == "picture")

    if out["start"] == "text":
        pic, pic_job = _queue_picture(db, cur, body, prompt)
        rec.update(picture={"generation_id": pic.generation_id, "media_id": pic.id, "status": "pending"},
                   source_media_id=pic.id)
        out.update(picture=pic, picture_job=pic_job)
    if better:
        made = _queue_back_views(db, cur, db.get(Generation, rec["source_id"]), title, *edit)
        rec["better_back"] = {"status": "pending", "views": {v: {"generation_id": i.generation_id, "media_id": i.id}
                                                             for v, (i, _) in made.items()}}
        out.update(better_back=True, back_views=[i for i, _ in made.values()], back_jobs=[j for _, j in made.values()])
    if out["start"] != "picture":
        rec["start"] = out["start"]

    params = {"model3d": rec, "model": engine.id, **chosen, **extra,
              "created_by": {"user_id": cur.id, "flow": "make_3d" if out["start"] == "picture" else
                             f"make_3d:{out['start']}"}}
    item = MediaItem(id=new_id(), workspace_id=ws, kind=m3.MEDIA_KIND, origin="generated",
                     title=title[:300], tags=[], folder_id=into, home=lib.home_of(body.part_of))
    db.add(item)
    db.flush()
    g = enqueue_generation(db, workspace_id=ws, project_id=None, target_type="media",
                           target_id=item.id, kind=m3.GEN_KIND, prompt=prompt, params=params,
                           seed=body.seed, parent_id=None)
    item.generation_id = g.id
    job = db.get(Job, g.job_id)
    db.commit()
    if out.get("picture") is not None:
        out["picture"] = lib.item_out(db, out["picture"])
        out["picture_job"] = job_out(out["picture_job"])
    if out.get("back_views"):
        out["back_views"] = [lib.item_out(db, i) for i in out["back_views"]]
        out["back_jobs"] = [job_out(j) for j in out["back_jobs"]]
    return Model3DOut(item=lib.item_out(db, item), job=job_out(job), model_resolved=engine.id, **out)


def _queue_back_views(db: Session, cur: CurrentUser, src: Generation, title: str, edit: mc.Model,
                      chosen: dict) -> dict:
    """Better back's side and back views: Character Studio's turnaround edit (Qwen edit plus the multiple-
    angles LoRA, the comfy driver's sheet_view) of the one picture, kept as working parts of the 3D Studio."""
    w, h = m3.back_view_size(src)
    made = {}
    for view, turn in m3.BACK_VIEWS:
        params = {"view": turn, "reference_ids": [src.id], "source_id": src.id, "angles_lora": 1.0, "width": w,
                  "height": h, "edit_model": edit.id, "model": edit.id, **chosen,
                  "created_by": {"user_id": cur.id, "flow": "make_3d:better_back"}}
        name = "side" if view == "left" else "back"
        item = MediaItem(id=new_id(), workspace_id=cur.workspace_id, kind="image", origin="generated",
                         title=f"{title} · {name} view"[:300], tags=[], width=w, height=h, home=m3.PART_HOME)
        db.add(item)
        db.flush()
        g = enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="media",
                               target_id=item.id, kind="sheet_view", prompt="", params=params)
        item.generation_id = g.id
        made[view] = (item, db.get(Job, g.job_id))
    return made


def _make_logo(db: Session, cur: CurrentUser, body: Model3DIn, into: str | None) -> Model3DOut:
    """A logo, sign or sticker: traced and extruded on the CPU (app.model3d_logo). A picture without
    transparency is cut out first on the image GPU, or keyed off its plain background when asked not to."""
    ws = cur.workspace_id
    media_id, src = _edit_source(db, ws, body.logo_id)
    source_item = db.get(MediaItem, media_id) if media_id else None
    title = (body.title or "").strip() or (f"{source_item.title} · 3D" if source_item and source_item.title
                                           else "Logo · 3D")
    if m3.has_transparency(get_settings().data_dir / src.file_path):
        alpha = "own"
    else:
        alpha = "cutout" if body.remove_background else "key"
    rec = {"start": "logo", "source_id": src.id, "source_media_id": media_id,
           "logo": (body.logo or Model3DLogo()).model_dump(), "alpha": alpha}
    out: dict = {"start": "logo"}
    if alpha == "cutout":
        cut, cut_job = _queue_cutout(db, cur, src, title)
        rec["cutout"] = {"generation_id": cut.generation_id, "media_id": cut.id, "status": "pending"}
        out.update(cutout=cut, cutout_job=cut_job)
    item = MediaItem(id=new_id(), workspace_id=ws, kind=m3.MEDIA_KIND, origin="generated", title=title[:300],
                     tags=[], folder_id=into, home=lib.home_of(body.part_of))
    db.add(item)
    db.flush()
    g = Generation(workspace_id=ws, project_id=None, target_type="media", target_id=item.id, kind=m3.GEN_KIND,
                   version=next_version(db, "media", item.id, m3.GEN_KIND), status="queued",
                   prompt=(src.prompt or "")[:4000], seed=body.seed if body.seed is not None else new_seed(),
                   params={"model3d": rec, "created_by": {"user_id": cur.id, "flow": "make_3d:logo"}})
    db.add(g)
    db.flush()
    # the general lane: no GPU, so it doesn't queue behind the pictures
    job = Job(workspace_id=ws, type=ml.JOB, generation_id=g.id, payload={"generation_id": g.id},
              message="Waiting for a worker")
    db.add(job)
    db.flush()
    g.job_id = job.id
    item.generation_id = g.id
    db.commit()
    if out.get("cutout") is not None:
        out["cutout"] = lib.item_out(db, out["cutout"])
        out["cutout_job"] = job_out(out["cutout_job"])
    return Model3DOut(item=lib.item_out(db, item), job=job_out(job), model_resolved="logo", **out)


def _queue_cutout(db: Session, cur: CurrentUser, src: Generation, title: str):
    from app.photo import restore

    try:
        params, _ = restore.build_restore(db, src, "cutout", {"edge": {"feather": 0.6, "shift": 0}})
    except restore.RestoreError as e:
        raise HTTPException(422, f"The logo can't be cut out here: {e}. Try a picture with a transparent "
                                 "background.") from None
    params["created_by"] = {"user_id": cur.id, "flow": "make_3d:logo"}
    return _new_image_item(db, cur, f"{title} · cut-out"[:300], src.prompt or "Cut-out", params, None, None,
                           m3.PART_HOME)


def _queue_picture(db: Session, cur: CurrentUser, body: Model3DIn, prompt: str):
    """The picture a text start is made from: an ordinary image generation on the default (or chosen) image
    model, kept as a working part of the 3D Studio. Package gates and quota see it like any other image."""
    model, chosen = rights.choose(db, body.image_model, "image", body.prompt or "")
    speed = mc.speed_of(model, None) if model.speeds else None
    w, h = lib.size_for_aspect("1:1")
    # off unless asked: an enhanced prompt likes to add a scene around the object
    mode = body.magic_prompt or ("on" if body.prompt_enhanced else "off")
    magic = prompt_enhance.marker(mode, body.prompt_enhanced, "image", brand_kit_id=body.brand_kit_id)
    params = {"aspect": "1:1", "width": w, "height": h, "style": None, "user_prompt": m3.object_head(body.prompt or ""),
              "negative": m3.OBJECT_NEGATIVE, "model": model.id, "magic_prompt": magic,
              "created_by": {"user_id": cur.id, "flow": "make_3d:text"}, **chosen}
    if speed:
        params["speed"] = speed.id
    prompt, params = brand.apply_to_request(db, cur.workspace_id, body.brand_kit_id, prompt, params)
    if params.get("brand"):
        # the kit's palette and look, but no logo pass: a logo would end up baked into the texture
        params["brand"] = {**params["brand"], "auto": False}
    return _new_image_item(db, cur, lib.short_title(body.prompt or ""), prompt, params, body.seed, None,
                           m3.PART_HOME)


# ---------------------------------------------------------------- turntable and thumbnail (part 3)

TURNTABLE_MAX = 60 * uploads.MB
SNAPSHOT_MAX = 10 * uploads.MB


def _sniff_turntable(head: bytes) -> str | None:
    t = uploads.sniff(head)
    return t if t in ("video/webm", "video/mp4") else None


def _sniff_snapshot(head: bytes) -> str | None:
    t = uploads.sniff(head)
    return t if t in ("image/png", "image/jpeg", "image/webp") else None


TURNTABLE = uploads.Profile(_sniff_turntable, lambda _t: TURNTABLE_MAX,
                            lambda _t: HTTPException(413, "A turntable can be at most 60 MB"),
                            "a WebM or MP4 video up to 60 MB", TURNTABLE_MAX)
SNAPSHOT = uploads.Profile(_sniff_snapshot, lambda _t: SNAPSHOT_MAX,
                           lambda _t: HTTPException(413, "A thumbnail can be at most 10 MB"),
                           "a PNG, JPEG or WebP picture up to 10 MB", SNAPSHOT_MAX)


def _ready_model(db: Session, workspace_id: str, media_id: str) -> tuple[MediaItem, Generation]:
    item = get_owned(db, MediaItem, media_id, workspace_id, "Model")
    if item.kind != m3.MEDIA_KIND:
        raise HTTPException(404, "Model not found")
    g = db.get(Generation, item.generation_id) if item.generation_id else None
    if g is None or g.status not in READY_STATES or not g.file_path:
        raise HTTPException(409, "The model isn't finished yet")
    return item, g


def _thumb_from(db: Session, g: Generation, im: Image.Image, source: str) -> None:
    thumbs.set_model_thumb(get_settings().data_dir / g.file_path, im)
    g.params = {**(g.params or {}), "thumb_from": source, "thumb_v": new_id()[:8]}
    db.commit()


def _store_turntable(db: Session, cur: CurrentUser, model: MediaItem, mg: Generation, got: uploads.Received,
                     folder, rel_folder: str) -> tuple[MediaItem, bool]:
    from app.catalogue import importer

    mp4 = got.media_type == "video/webm" and m3.webm_to_mp4(got)
    got.title = f"{model.title or '3D model'} · turntable"
    got.fields = {}
    item, _ = importer.store(db, cur.workspace_id, cur.id, got, folder, rel_folder)
    # made in the studio, not brought in: it belongs with the generated work
    item.origin = "generated"
    tg = db.get(Generation, item.generation_id)
    tg.params = {**tg.params, "model3d_of": model.id,
                 "created_by": {"user_id": cur.id, "flow": "make_3d:turntable"}}
    # every turntable, so deleting the model can offer to take them with it
    done = [t for t in (mg.params or {}).get("turntables") or [] if t != item.id]
    mg.params = {**(mg.params or {}), "turntable_id": item.id, "turntables": [*done, item.id]}
    db.commit()
    # the first frame takes over from the source picture, unless someone picked a view by hand
    if (mg.params or {}).get("thumb_from") != "snapshot":
        im = thumbs.first_frame(get_settings().data_dir / tg.file_path)
        if im is not None:
            with im:
                _thumb_from(db, mg, im, "turntable")
    return item, mp4


@router.post("/3d/{media_id}/turntable", response_model=Turntable3DOut, status_code=201,
             openapi_extra=multipart(file_help="The turntable the viewer recorded: WebM or MP4, at most 60 MB"),
             summary="Save a model's turntable",
             description="Stores the video as a new Library item (kind `video`, a final output) linked to the "
                         "model by `params.model3d_of`. A WebM is converted to H.264 MP4 when ffmpeg can. Its "
                         "first frame becomes the model's thumbnail unless one was chosen from the viewer.")
async def save_turntable(media_id: str, request: Request, db: Session = Depends(get_db),
                         cur: CurrentUser = Depends(require_editor)):
    model, mg = await run_in_threadpool(_ready_model, db, cur.workspace_id, media_id)
    folder, rel_folder = lib.media_dir(cur.workspace_id)
    got = await uploads.receive(request, folder, TURNTABLE)
    try:
        item, mp4 = await run_in_threadpool(_store_turntable, db, cur, model, mg, got, folder, rel_folder)
    except BaseException:
        got.path.unlink(missing_ok=True)
        raise
    out = await run_in_threadpool(lambda: (lib.item_out(db, item), lib.item_out(db, model)))
    return Turntable3DOut(item=out[0], model=out[1], transcoded=mp4)


@router.post("/3d/{media_id}/thumbnail", response_model=MediaItemOut,
             openapi_extra=multipart(file_help="A snapshot of the viewer: PNG, JPEG or WebP, at most 10 MB"),
             summary="Set a model's thumbnail",
             description="Replaces the model's Library thumbnail with a picture of it, usually the viewer's "
                         "current view. Answers with the model and its new `thumb_url`.")
async def set_thumbnail(media_id: str, request: Request, db: Session = Depends(get_db),
                        cur: CurrentUser = Depends(require_editor)):
    model, mg = await run_in_threadpool(_ready_model, db, cur.workspace_id, media_id)
    folder, _ = lib.media_dir(cur.workspace_id)
    got = await uploads.receive(request, folder, SNAPSHOT)
    try:
        await run_in_threadpool(_snapshot, db, mg, got)
    finally:
        got.path.unlink(missing_ok=True)
    return await run_in_threadpool(lib.item_out, db, model)


def _snapshot(db: Session, mg: Generation, got: uploads.Received) -> None:
    uploads.probe_image(got.path, got.media_type)
    with Image.open(got.path) as im:
        im.load()
        _thumb_from(db, mg, im, "snapshot")


# ---------------------------------------------------------------- product shots for Ad Studio (part 4)

SHOTS_MAX = 6
SHOT_MAX = 15 * uploads.MB
AD_HOME = "ad"
# the viewer's angle ids, sent as each file's name (front.png); the words go in the title
SHOT_ANGLES = {"front": "front", "left": "three-quarter left", "right": "three-quarter right", "side": "side",
               "top": "from above", "back": "back"}


def _needs_ads(db: Session, cur: CurrentUser) -> None:
    from app import packages as pk
    from app import quota
    from app.models import User

    lim = quota.limits_for(db, db.get(User, cur.id))
    for f in ("3d", "ads"):
        if not lim.allows(f):
            name = lim.package.name if lim.package else "Your"
            raise HTTPException(403, f"The {name} package doesn't include {pk.FEATURES[f]}")


async def _read_shots(request: Request) -> list[tuple[str, bytes]]:
    from starlette.datastructures import UploadFile

    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > SHOTS_MAX * SHOT_MAX + uploads.MB:
        raise HTTPException(413, f"Send at most {SHOTS_MAX} pictures of up to 15 MB each")
    form = await request.form(max_files=SHOTS_MAX + 1, max_fields=10)
    shots: list[tuple[str, bytes]] = []
    for _, v in form.multi_items():
        if not isinstance(v, UploadFile):
            continue
        if len(shots) == SHOTS_MAX:
            raise HTTPException(422, f"Send at most {SHOTS_MAX} angles")
        angle = Path(v.filename or "").stem.lower()
        if angle not in SHOT_ANGLES:
            raise HTTPException(422, f"Name each file after its angle: {', '.join(SHOT_ANGLES)} (e.g. front.png)")
        if angle in (a for a, _ in shots):
            raise HTTPException(422, f"The {SHOT_ANGLES[angle]} angle was sent twice")
        data = await v.read(SHOT_MAX + 1)
        if len(data) > SHOT_MAX:
            raise HTTPException(413, "A product shot can be at most 15 MB")
        if not data:
            raise HTTPException(422, f"The {SHOT_ANGLES[angle]} picture is empty")
        if uploads.sniff(data[:uploads.SNIFF_BYTES]) != "image/png":
            raise HTTPException(415, "Product shots are PNG pictures")
        shots.append((angle, data))
    if not shots:
        raise HTTPException(422, "Attach the angles to keep as PNG files in fields named 'file'")
    return shots


def _store_shots(db: Session, cur: CurrentUser, model: MediaItem, shots: list[tuple[str, bytes]]) -> list[MediaItem]:
    import hashlib

    from app.catalogue import importer

    folder, rel_folder = lib.media_dir(cur.workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    got = []
    try:
        for angle, data in shots:
            tmp = folder / f".shot-{new_id()}.part"
            tmp.write_bytes(data)
            got.append(uploads.Received(tmp, "image/png", len(data), f"{angle}.png",
                                        f"{model.title or '3D model'} · {SHOT_ANGLES[angle]}"[:300],
                                        hashlib.sha256(data).hexdigest(), {}))
        # every one is checked before any is kept, so a bad sixth doesn't leave five behind
        for r in got:
            uploads.probe_image(r.path, r.media_type)
        items = []
        for (angle, _), r in zip(shots, got):
            item, _ = importer.store(db, cur.workspace_id, cur.id, r, folder, rel_folder)
            # rendered in the studio, and a working part of Ad Studio rather than a Library picture
            item.origin = "generated"
            item.home = AD_HOME
            g = db.get(Generation, item.generation_id)
            g.params = {**g.params, "model3d_of": model.id, "angle": angle,
                        "created_by": {"user_id": cur.id, "flow": "make_3d:shots"}}
            items.append(item)
        db.commit()
        return items
    finally:
        for r in got:
            r.path.unlink(missing_ok=True)


@router.post("/3d/{media_id}/shots", response_model=Shots3DOut, status_code=201,
             openapi_extra=multipart(file_help="1–6 PNG renders, each at most 15 MB, named after the angle: "
                                               "front, left, right, side, top or back (front.png)", many=True),
             summary="Save product shots of a model for Ad Studio",
             description="Stores the viewer's renders as pictures for Ad Studio's product photos: kind `image`, "
                         "`home` \"ad\" (working parts, kept out of the general lists), titled "
                         "\"<model title> · <angle>\" and linked to the model by `params.model3d_of`. Needs "
                         "both the 3D Studio and Ad Studio in the package.")
async def save_shots(media_id: str, request: Request, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    model, _ = await run_in_threadpool(_ready_model, db, cur.workspace_id, media_id)
    await run_in_threadpool(_needs_ads, db, cur)
    shots = await _read_shots(request)
    items = await run_in_threadpool(_store_shots, db, cur, model, shots)
    out = await run_in_threadpool(lambda: [lib.item_out(db, i) for i in items])
    return Shots3DOut(items=out, ids=[i.id for i in out])
