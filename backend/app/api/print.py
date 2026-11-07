"""Print Studio (contract v41): signs, boards, flyers and window stickers at real sizes. Admins only, signed in
(not API tokens), and left out of the OpenAPI document; everyone else gets a 404."""
import copy
import io
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.limits import PROMPT_MAX
from app import brand as br
from app import models_catalog as mc
from app import visibility as vis
from app.ads import service as ads
from app.api.ads import _kit, _products
from app.brand_schemas import norm_hex
from app.db import get_db
from app.models import AdCampaign, Generation, new_id
from app.print_studio import export as ex
from app.print_studio import fonts as pfonts
from app.print_studio import layout as pl
from app.print_studio import service as ps
from app.print_studio import sizes as sz
from app.schemas import JobOut
from app.security import CurrentUser, require_admin, session_only
from app.services import generation_file, get_owned, job_out

router = APIRouter(prefix="/print", tags=["print"], dependencies=[Depends(require_admin)])

Job_ = Literal["sign", "board", "flyer", "sticker"]
Distance = Literal["near", "street", "road"]
PREVIEW_MAX = 960
ALL_LAYOUTS = tuple(x for v in pl.LAYOUTS.values() for x in v)


class LinesIn(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="The shop's name, set exactly as written")
    tagline: str = Field("", max_length=160)
    offers: list[str] = Field([], max_length=12, description="Offer or price lines, set exactly as written")
    opening_hours: str = Field("", max_length=120)
    phone: str = Field("", max_length=60)
    address: str = Field("", max_length=200)
    website: str = Field("", max_length=200)

    @field_validator("offers")
    @classmethod
    def _offers(cls, v):
        out = []
        for o in v:
            o = " ".join((o or "").split())[:160]
            if o and o not in out:
                out.append(o)
        return out


class CreateIn(BaseModel):
    title: str = Field("", max_length=200)
    job: Job_ = "sign"
    width_cm: float = Field(ge=sz.MIN_CM, le=sz.MAX_CM)
    height_cm: float = Field(ge=sz.MIN_CM, le=sz.MAX_CM)
    dpi: Literal[100, 150, 200, 300] | None = Field(None, description="Default: 150 for signs and boards, 300 for "
                                                                       "flyers and stickers")
    bleed_mm: float = Field(sz.DEFAULT_BLEED_MM, ge=0, le=20)
    shape: Literal["rect", "round"] = "rect"
    viewing_distance: Distance | None = Field(None, description="Drives the smallest letters; default by job")
    business_type: str = Field("", max_length=160, description='e.g. "livs / mini market"')
    lines: LinesIn
    language: str = Field("en", min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")
    style: str = Field("", max_length=PROMPT_MAX, description="A style hint, e.g. fresh and bright, bold clean lettering")
    brief: str = Field("", max_length=PROMPT_MAX)
    brand_kit_id: str | None = Field(None, max_length=64)
    product_media_ids: list[str] = Field([], max_length=3, description="Product photos the picture is built round")
    photo_media_ids: list[str] = Field([], max_length=6, description="The owner's own photos, used as they are")
    image_model: str = Field(ads.AUTO_MODEL, max_length=60)
    variants: int = Field(3, ge=1, le=6, description="Design options")
    families: list[Literal["typographic", "photo", "illustration"]] | None = Field(None, max_length=6)
    font_preset: str | None = Field(None, max_length=20)
    quality: Literal["draft", "final"] = "final"
    review: bool = Field(False, description="Stop after the concepts for an OK")
    export_all: bool = Field(False, description="Make every option's print files straight away")
    print_safe: bool = Field(False, description="Tone down colours that print dull")

    @model_validator(mode="after")
    def _shape(self):
        if self.shape == "round" and abs(self.width_cm - self.height_cm) > 0.01:
            raise ValueError("A round sticker is as wide as it is tall")
        return self


class ConceptPatch(BaseModel):
    approved: bool | None = None
    headline: str | None = Field(None, max_length=120)
    cta: str | None = Field(None, max_length=60)


class DesignTexts(BaseModel):
    name: str | None = Field(None, max_length=120)
    tagline: str | None = Field(None, max_length=160)
    headline: str | None = Field(None, max_length=120)
    offers: list[str] | None = Field(None, max_length=12)
    hours: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=60)
    address: str | None = Field(None, max_length=200)
    website: str | None = Field(None, max_length=200)
    cta: str | None = Field(None, max_length=60)


class LogoIn(BaseModel):
    position: Literal["auto", "none"] | None = None
    variant: Literal["auto", "primary", "light", "dark"] | None = None


class FocusIn(BaseModel):
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


class DesignPatch(BaseModel):
    texts: DesignTexts | None = None
    layout: str | None = Field(None, max_length=30)
    decor: Literal["none", "awning", "frame"] | None = None
    hero: Literal["name", "headline"] | None = None
    caps: bool | None = None
    font_preset: str | None = Field(None, max_length=20)
    display_font: str | None = Field(None, max_length=20, description="The name's face on its own; '' follows the type")
    colours: dict[Literal["ground", "ink", "accent"], str] | None = None
    logo: LogoIn | None = None
    focus: FocusIn | None = None
    print_safe: bool | None = None
    reset: bool = Field(False, description="Drop every edit of this design first")

    @field_validator("colours")
    @classmethod
    def _hex(cls, v):
        return {k: norm_hex(c) for k, c in v.items()} if v else v


class RegenerateIn(BaseModel):
    what: Literal["picture", "copy"]


class FilesIn(BaseModel):
    design_ids: list[str] | None = Field(None, max_length=6, description="Empty: every finished option")


class ResizeIn(BaseModel):
    width_cm: float = Field(ge=sz.MIN_CM, le=sz.MAX_CM)
    height_cm: float = Field(ge=sz.MIN_CM, le=sz.MAX_CM)
    job: Job_ | None = None
    dpi: Literal[100, 150, 200, 300] | None = None
    bleed_mm: float | None = Field(None, ge=0, le=20)
    viewing_distance: Distance | None = None
    export_all: bool = False


def _job(db: Session, cur: CurrentUser, jid: str) -> AdCampaign:
    return ps.ensure_print(get_owned(db, AdCampaign, jid, cur.workspace_id, "Print job"))


def _size_ok(width_cm: float, height_cm: float, dpi: int, bleed: float) -> None:
    cv = sz.canvas(width_cm, height_cm, dpi, bleed)
    if sz.too_big(cv):
        best = sz.best_dpi(width_cm, height_cm, dpi, bleed)
        raise HTTPException(422, f"{cv.label()} is {cv.megapixels:.0f} megapixels, more than one file can hold "
                                 f"({sz.MAX_MP:.0f}); use {best} dpi for this size")


def _preset_ok(p: str | None, kit) -> None:
    if p and p not in pfonts.PRESETS and not (p == "brand" and kit is not None and kit.font_files):
        raise HTTPException(422, f"Unknown font preset '{p}'")


@router.get("/options")
@session_only
def options(kit_id: str | None = None, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    kit = br.get_kit(db, cur.workspace_id, kit_id) if kit_id else None
    models = [{"id": ads.AUTO_MODEL, "label": "Auto (photoreal)", "default": True}]
    models += [{"id": m, "label": mc.MODELS[m].label, "default": False}
               for m in ads.IMAGE_MODELS if m in mc.MODELS and not mc.hidden(mc.MODELS[m])]
    return {"jobs": [{"id": j, "label": sz.JOB_LABELS[j], "dpi": sz.DEFAULT_DPI[j],
                      "distance": sz.DEFAULT_DISTANCE[j]} for j in sz.JOBS],
            "presets": list(sz.PRESETS), "dpis": list(sz.DPIS), "bleed_mm": sz.DEFAULT_BLEED_MM, "max_mp": sz.MAX_MP,
            "distances": [{"id": k, "label": sz.DISTANCE_LABELS[k], "metres": v, "min_cap_mm": sz.min_cap_mm(k)}
                          for k, v in sz.DISTANCES.items()],
            "families": [{"id": f, "label": {"typographic": "Type only", "photo": "Photo-led",
                                             "illustration": "Illustration"}[f]} for f in pl.DESIGN_FAMILIES],
            "layouts": {fam: [{"id": x, "label": pl.LAYOUT_LABELS[x], "photo": x in pl.PHOTO_LAYOUTS} for x in v]
                        for fam, v in pl.LAYOUTS.items()},
            "decor": [{"id": "none", "label": "None"}, {"id": "awning", "label": "Awning stripes"},
                      {"id": "frame", "label": "Frame"}],
            "font_presets": pfonts.presets_out(bool(kit is not None and kit.font_files)),
            "display_fonts": pfonts.display_out(),
            "image_models": models, "variants": {"min": 1, "max": 6, "default": 3},
            "formats": [{"id": f, "label": {"pdf": "PDF (print)", "png": "PNG", "jpg": "JPG", "svg": "SVG (editable)",
                                            "zip": "Layers (ZIP)"}[f]} for f in ex.FORMATS]}


@router.post("", status_code=201)
@session_only
def create_job(body: CreateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    kit = _kit(db, cur.workspace_id, body.brand_kit_id)
    _preset_ok(body.font_preset, kit)
    _products(db, cur.workspace_id, body.product_media_ids + body.photo_media_ids)
    if body.product_media_ids:
        mc.get(mc.AUTO, "edit")
    else:
        mc.get(body.image_model, "image")
    data = body.model_dump()
    data["dpi"] = body.dpi or sz.DEFAULT_DPI[body.job]
    data["viewing_distance"] = body.viewing_distance or sz.DEFAULT_DISTANCE[body.job]
    _size_ok(body.width_cm, body.height_cm, data["dpi"], body.bleed_mm)
    c = ps.create(db, cur, data, kit)
    ps.queue(db, c)
    db.commit()
    return ps.campaign_out(db, c)


@router.get("")
@session_only
def list_jobs(db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    rows = db.scalars(select(AdCampaign).where(AdCampaign.workspace_id == cur.workspace_id,
                                               AdCampaign.kind == ps.KIND, *vis.where(AdCampaign, vis.wanted(cur)))
                      .order_by(AdCampaign.updated_at.desc()).limit(200)).all()
    return [ps.summary_out(db, c) for c in rows]


@router.get("/{job_id}")
@session_only
def get_job(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    return ps.campaign_out(db, _job(db, cur, job_id))


@router.delete("/{job_id}", status_code=204)
@session_only
def delete_job(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    ads.ensure_idle(db, c)
    db.delete(c)
    db.commit()
    return Response(status_code=204)


@router.post("/{job_id}/continue", response_model=JobOut, status_code=202)
@session_only
def continue_job(job_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    if c.concepts and not any(x.get("approved") for x in c.concepts):
        raise HTTPException(422, "Tick at least one option to make")
    for k in ps.STAGE_KEYS:
        rec = ps.stage(c, k)
        if rec.get("status") == "failed":
            rec.update(status="pending", detail=None)
            ps.put_stage(c, k, rec)
    if ps.done(c, "layout") and any(x.get("approved") and not ps.designs_of(c, x["id"]) for x in c.concepts or []):
        ps.reset_from(c, "pictures")
    job = ps.queue(db, c)
    db.commit()
    return job_out(job)


@router.patch("/{job_id}/concepts/{concept_id}")
@session_only
def patch_concept(job_id: str, concept_id: str, body: ConceptPatch, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    ads.ensure_idle(db, c)
    con = ads.concept(c, concept_id)
    changed = False
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "approved":
            con["approved"] = bool(v)
        elif con.get(k) != " ".join((v or "").split()):
            con[k] = " ".join((v or "").split())
            changed = True
    con["edited"] = con.get("edited") or changed
    ads.put_concept(c, con)
    if changed and ps.designs_of(c, con["id"]):
        ps.render_concept(db, c, ads.concept(c, con["id"]), ads.kit_of(db, c), only_dirty=False)
    ps.touch(c)
    db.commit()
    return ps.campaign_out(db, c)


@router.post("/{job_id}/concepts/{concept_id}/regenerate")
@session_only
def regenerate(job_id: str, concept_id: str, body: RegenerateIn, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    ads.ensure_idle(db, c)
    con = ads.concept(c, concept_id)
    if body.what == "copy":
        con["copy_dirty"] = True
        ads.put_concept(c, con)
        ps.reset_from(c, "concepts")
    else:
        if not ps.groups_of(con):
            raise HTTPException(409, "This option is type only; it has no picture to make again")
        con.update(photos={}, error=None)
        ads.put_concept(c, con)
        ads.mark_dirty(c, con["id"])
        ps.reset_from(c, "pictures")
    ps.queue(db, c)
    db.commit()
    return ps.campaign_out(db, c)


def _apply(rec: dict, body: DesignPatch) -> dict:
    o = {} if body.reset else dict(rec.get("overrides") or {})
    if body.texts is not None:
        t = {**(o.get("texts") or {}), **body.texts.model_dump(exclude_unset=True)}
        o["texts"] = {k: v for k, v in t.items() if v is not None}
    for k in ("layout", "decor", "hero", "font_preset", "display_font"):
        v = getattr(body, k)
        if v is not None:
            o[k] = v
    for k in ("caps", "print_safe"):
        v = getattr(body, k)
        if v is not None:
            o[k] = v
    if body.colours is not None:
        o["colours"] = {**(o.get("colours") or {}), **body.colours}
    if body.logo is not None:
        o["logo"] = {**(o.get("logo") or {}), **body.logo.model_dump(exclude_none=True)}
    if body.focus is not None:
        o["focus"] = body.focus.model_dump()
    return o


def _check_patch(c: AdCampaign, body: DesignPatch, kit) -> None:
    _preset_ok(body.font_preset, kit)
    if body.display_font and body.display_font not in pfonts.DISPLAY:
        raise HTTPException(422, f"Unknown display font '{body.display_font}'")
    if body.layout is not None and body.layout not in pl.LAYOUTS[ps.shape_family(ps.brief_of(c))]:
        raise HTTPException(422, f"Layout '{body.layout}' doesn't fit this shape")


@router.patch("/{job_id}/designs/{design_id}")
@session_only
def patch_design(job_id: str, design_id: str, body: DesignPatch, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    ads.ensure_idle(db, c)
    rec = ps.design(c, design_id)
    con = ads.concept(c, rec["concept_id"])
    kit = ads.kit_of(db, c)
    _check_patch(c, body, kit)
    rec["overrides"] = _apply(rec, body)
    rec = ps.write_design(db, c, con, rec, kit)
    ps.touch(c)
    db.commit()
    return ps.design_out(db, c, rec, con, kit)


@router.post("/{job_id}/designs/{design_id}/preview", response_class=Response)
@session_only
def preview_design(job_id: str, design_id: str, body: DesignPatch, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    rec = ps.design(c, design_id)
    con = ads.concept(c, rec["concept_id"])
    kit = ads.kit_of(db, c)
    _check_patch(c, body, kit)
    rec["overrides"] = _apply(rec, body)
    img, report = ps.render_preview(db, ps.spec_for(c, con, rec, kit), kit)
    img.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=3)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/{job_id}/files", response_model=JobOut, status_code=202)
@session_only
def make_files(job_id: str, body: FilesIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    ready = [a["id"] for a in c.ads or [] if a.get("status") == "ready"]
    want = body.design_ids or ready
    unknown = [d for d in want if d not in ready]
    if unknown:
        raise HTTPException(404 if not ready else 409, "That option isn't laid out yet")
    if not want:
        raise HTTPException(409, "No finished options yet")
    job = ps.queue_files(db, c, want)
    db.commit()
    return job_out(job)


def _filename(c: AdCampaign, g: Generation) -> str:
    stem = (g.params or {}).get("title") or "print"
    ext = (g.file_path or "").rsplit(".", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{stem}.{ext}")


@router.get("/{job_id}/designs/{design_id}/files/{fmt}")
@session_only
def download(job_id: str, design_id: str, fmt: Literal["pdf", "png", "jpg", "svg", "zip"],
             db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    rec = ps.design(c, design_id)
    g = db.get(Generation, ((rec.get("files") or {}).get("ids") or {}).get(fmt) or "")
    path = generation_file(g) if g is not None and g.workspace_id == c.workspace_id else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Make the print files first")
    return FileResponse(path, media_type=g.media_type, filename=_filename(c, g),
                        headers={"Cache-Control": "private, no-store"})


@router.post("/{job_id}/designs/{design_id}/resize", status_code=201)
@session_only
def other_size(job_id: str, design_id: str, body: ResizeIn, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_admin)):
    c = _job(db, cur, job_id)
    return resize(db, cur, c, design_id, body)


def resize(db: Session, cur: CurrentUser, c: AdCampaign, design_id: str, body: ResizeIn) -> dict:
    """Make another size from a design: a new print job with the same copy, look and pictures, laid out again
    for the new shape (the pictures are upscaled again for it)."""
    rec = ps.design(c, design_id)
    con = copy.deepcopy(ads.concept(c, rec["concept_id"]))
    b = dict(c.brief or {})
    job = body.job or b.get("job") or "sign"
    b.update(width_cm=body.width_cm, height_cm=body.height_cm, job=job, export_all=body.export_all,
             variants=1, review=False,
             dpi=body.dpi or (b.get("dpi") if body.job is None else sz.DEFAULT_DPI[job]) or sz.DEFAULT_DPI[job],
             bleed_mm=body.bleed_mm if body.bleed_mm is not None else b.get("bleed_mm", sz.DEFAULT_BLEED_MM),
             viewing_distance=body.viewing_distance or (b.get("viewing_distance") if body.job is None
                                                        else sz.DEFAULT_DISTANCE[job]) or "street")
    if b.get("shape") == "round" and abs(body.width_cm - body.height_cm) > 0.01:
        b["shape"] = "rect"
    _size_ok(body.width_cm, body.height_cm, int(b["dpi"]), float(b["bleed_mm"]))
    kit = ads.kit_of(db, c)
    name = (b.get("lines") or {}).get("name") or "Print job"
    new = ps.create(db, cur, {**b, "language": c.language,
                              "title": f"{name} · {sz.cm(body.width_cm * 10)} × {sz.cm(body.height_cm * 10)} cm"}, kit)
    nb = ps.brief_of(new)
    allowed = ps.layouts_for(nb, con.get("family") or "typographic", ps.kit_logo(kit))
    for grp, pic in list((con.get("photos") or {}).items()):
        # the same picture, upscaled again for what the new size needs
        con["photos"][grp] = {k: v for k, v in pic.items() if k not in ("final", "upscale", "upscale_tries")}
    con.update(id=new_id(), n=1, approved=True, copy_dirty=False,
               layout=con.get("layout") if con.get("layout") in allowed else allowed[0])
    new.concepts = [con]
    o = {k: v for k, v in (rec.get("overrides") or {}).items() if k not in ("layout", "focus")}
    d = {**ps.new_design(con), "overrides": o}
    new.ads = [d]
    ps.put_stage(new, "concepts", {"status": "done", "approved": True})
    new.stage = "pictures"
    new.brief = {k: v for k, v in b.items() if k in ps.DEFAULTS}
    ps.queue(db, new)
    db.commit()
    return ps.campaign_out(db, new)
