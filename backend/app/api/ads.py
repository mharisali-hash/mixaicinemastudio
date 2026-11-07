"""Ad Studio (contract v36): brand-aware adverts from a brief, a photo per concept and exact typography."""
import io
import json
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.limits import PROMPT_MAX
from app import brand as br
from app import models_catalog as mc
from app import visibility as vis
from app.ads import fonts as fz
from app.ads import layout as lay
from app.ads import service as svc
from app.brand_schemas import norm_hex
from app.db import get_db
from app.llm.prompts import AD_LAYOUTS
from app.models import AdCampaign, BrandKit, Generation
from app.schemas import JobOut
from app.security import CurrentUser, get_current_user, require_editor
from app.services import generation_file, get_owned, job_out

router = APIRouter(prefix="/ads", tags=["ads"])

Format = Literal["4:5", "9:16", "1:1", "16:9", "1.91:1", "2:3", "yt"]
ContactKey = Literal["phone", "whatsapp", "email", "website", "booking_url", "location", "opening_hours"]
Style = Literal["frosted", "side_wash", "bottom_bar", "split", "badge", "minimal", "cutout_blob", "arch_mask",
                "circle_mask", "big_price", "editorial", "before_after", "quote"]
MaskShape = Literal["arch", "circle", "teardrop", "blob"]
Line = Field(None, max_length=300)
PREVIEW_MAX = 960


class FooterItems(BaseModel):
    contact: list[ContactKey] | None = Field(None, description="Contact fields to show, in any order")
    socials: list[str] | None = Field(None, max_length=10, description='Social keys, "platform:handle"')


class FollowIn(BaseModel):
    show: bool | None = None
    text: str | None = Field(None, max_length=40, description='Empty: "Follow us" in the ad language')


class QuoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=300, description="A customer's own words, used verbatim")
    by: str = Field("", max_length=80)


class CreateIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "subject": "Weekly home cleaning", "description": "Trusted cleaners in Stockholm, every week",
        "occasion": "autumn", "market": "Sweden", "language": "sv", "tone": "warm, cosy, confident",
        "offers": ["Från 215 kr/timme", "Ingen bindningstid"], "formats": ["4:5", "9:16"], "variants": 3}]})

    title: str = Field("", max_length=200)
    brand_kit_id: str | None = Field(None, max_length=64, description="None: no brand")
    subject: str = Field(min_length=1, max_length=200, description="The service or product")
    description: str = Field("", max_length=PROMPT_MAX)
    brief: str = Field("", max_length=PROMPT_MAX, description="Free text; empty lets the creative director decide")
    occasion: str = Field("", max_length=120)
    market: str = Field("", max_length=120, description="Country or region")
    language: str = Field("en", min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[a-z]{2,4})?$")
    tone: str = Field("", max_length=120)
    must_say: str = Field("", max_length=200)
    offers: list[str] = Field([], max_length=6, description="The only offers the copy may use")
    formats: list[Format] = Field(["4:5"], min_length=1, max_length=7)
    variants: int = Field(3, ge=2, le=8, description="How many concepts")
    product_media_ids: list[str] = Field([], max_length=3, description="Photos of the real product")
    # "auto": each concept picks a photoreal engine (Z-Image Turbo or Qwen-Image 2512); any other id is kept
    image_model: str = Field(svc.AUTO_MODEL, max_length=60)
    font_preset: str | None = Field(None, max_length=20)
    review: bool = Field(True, description="Stop after the concepts for an OK")
    logo_on_surface: bool = True
    footer_items: FooterItems | None = Field(None, description="What the footers show; default: website, "
                                                             "booking link, phone, WhatsApp, socials for ads")
    quality: Literal["draft", "final"] = Field("final", description="draft: one photo each, no upscale, no vision QA")
    candidates: int = Field(3, ge=1, le=4, description="Photos made per concept and orientation on final")
    quotes: list[QuoteIn] = Field([], max_length=6, description="Testimonials for the quote layout, verbatim")
    use_style: bool = Field(True, description="Follow the brand kit's style references when it has some")

    @field_validator("offers")
    @classmethod
    def _offers(cls, v):
        out = []
        for o in v:
            o = " ".join((o or "").split())[:160]
            if o and o not in out:
                out.append(o)
        return out


class ConceptPatch(BaseModel):
    approved: bool | None = None
    kicker: str | None = Line
    headline: str | None = Field(None, min_length=1, max_length=300)
    body: str | None = Field(None, max_length=600)
    cta: str | None = Field(None, max_length=60)
    offers: list[str] | None = Field(None, max_length=3)
    layout: Style | None = None
    photo_prompt: str | None = Field(None, min_length=1, max_length=PROMPT_MAX)
    key_word: str | None = Field(None, max_length=60)
    quote: int | None = Field(None, ge=1, le=6)


class RegenerateIn(BaseModel):
    what: Literal["copy", "photo", "layout"]
    layout: Style | None = Field(None, description="what=layout: this style; default the next one")


class AdTexts(BaseModel):
    kicker: str | None = Line
    headline: str | None = Line
    body: str | None = Field(None, max_length=600)
    offers: list[str] | None = Field(None, max_length=3)
    cta: str | None = Field(None, max_length=60)
    badge_big: str | None = Field(None, max_length=12)
    badge_small: str | None = Field(None, max_length=20)
    highlight: str | None = Field(None, max_length=60, description="The key word set in the accent colour")
    quote: str | None = Field(None, max_length=300)
    quote_by: str | None = Field(None, max_length=80)


class LogoIn(BaseModel):
    position: Literal["tl", "tr", "tc", "bl", "br", "stack", "none"] | None = None
    variant: Literal["auto", "primary", "light", "dark"] | None = None


class FocusIn(BaseModel):
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


class ShapeIn(BaseModel):
    mask: MaskShape | None = None
    colour: str | None = None
    seed: int | None = Field(None, ge=0, le=9999)

    @field_validator("colour")
    @classmethod
    def _hex(cls, v):
        return norm_hex(v) if v else v


class PlacementIn(BaseModel):
    flip: bool | None = Field(None, description="true: the copy on the other side; null: placed automatically")


class AdPatch(BaseModel):
    texts: AdTexts | None = None
    style: Style | None = None
    font_preset: str | None = Field(None, max_length=20)
    colours: dict[Literal["text", "accent", "button", "button_text", "footer", "footer_text", "pill", "block"],
                  str] | None = None
    logo: LogoIn | None = None
    footer: bool | None = None
    footer_items: FooterItems | None = None
    follow: FollowIn | None = None
    focus: FocusIn | None = None
    shape: ShapeIn | None = None
    placement: PlacementIn | None = None
    reset: bool = Field(False, description="Drop every edit of this ad first")

    @field_validator("colours")
    @classmethod
    def _hex(cls, v):
        return {k: norm_hex(c) for k, c in v.items()} if v else v


def _camp(db: Session, cur: CurrentUser, cid: str) -> AdCampaign:
    c = get_owned(db, AdCampaign, cid, cur.workspace_id, "Ad campaign")
    if c.kind == "print":
        # a Print Studio job (admin only) is never an ad campaign here
        raise HTTPException(404, "Ad campaign not found")
    return c


def _kit(db: Session, ws: str, kit_id: str | None) -> BrandKit | None:
    # no kit asked for means no brand: falling back to the default kit put one client's logo on another's sign
    if not kit_id:
        return None
    kit = br.get_kit(db, ws, kit_id)
    if kit is None:
        raise HTTPException(404, "Brand kit not found")
    return kit


def _check_font_preset(p: str | None, kit: BrandKit | None) -> None:
    if p and p not in fz.PRESETS and not (p == fz.BRAND and kit is not None and kit.font_files):
        raise HTTPException(422, f"Unknown font preset '{p}'")


def _products(db: Session, ws: str, ids: list[str]) -> None:
    for mid in ids:
        g = br.media_generation(db, ws, mid)
        if g is None or not (g.media_type or "").startswith("image/"):
            raise HTTPException(422, f"Product photo {mid} isn't a finished picture in your library")


@router.get("/options", summary="What the brief form and the ad editor offer")
def options(kit_id: str | None = None, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    kit = br.get_kit(db, cur.workspace_id, kit_id) if kit_id else None
    models = [{"id": svc.AUTO_MODEL, "label": "Auto (photoreal, per concept)", "default": True}]
    models += [{"id": m, "label": mc.MODELS[m].label, "default": False}
               for m in svc.IMAGE_MODELS if m in mc.MODELS and not mc.hidden(mc.MODELS[m])]
    return {"formats": [{"id": f, "label": lay.FORMAT_LABELS[f], "width": w, "height": h}
                        for f, (w, h) in lay.FORMATS.items()],
            "styles": [{"id": s, "label": lay.STYLE_LABELS[s], "needs": lay.STYLE_NEEDS.get(s)} for s in lay.STYLES],
            "mask_shapes": ["arch", "circle", "teardrop", "blob"], "qualities": ["draft", "final"],
            "candidates": {"min": 1, "max": 4, "default": 3},
            "font_presets": fz.presets_out(bool(kit is not None and kit.font_files)),
            "logo_positions": list(lay.LOGO_POSITIONS), "image_models": models,
            "variants": {"min": 2, "max": 8, "default": 3}, "product_photos": {"max": 3}}


@router.post("", status_code=201, summary="Start an ad campaign",
             description="Writes the concepts straight away. With review on it stops there for POST /continue "
                         "(edit or untick concepts first); with review off it makes the photos and ads in one go.")
def create_campaign(body: CreateIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    kit = _kit(db, cur.workspace_id, body.brand_kit_id)
    _check_font_preset(body.font_preset, kit)
    _products(db, cur.workspace_id, body.product_media_ids)
    if body.product_media_ids:
        mc.get(mc.AUTO, "edit")
    else:
        mc.get(body.image_model, "image")  # 422 when it isn't an image model here
    c = svc.create(db, cur, body.model_dump(), kit)
    svc.queue(db, c)
    db.commit()
    return svc.campaign_out(db, c)


@router.get("", summary="List ad campaigns")
def list_campaigns(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(AdCampaign).where(AdCampaign.workspace_id == cur.workspace_id, AdCampaign.kind == "ad",
                                               *vis.where(AdCampaign, vis.wanted(cur)))
                      .order_by(AdCampaign.updated_at.desc()).limit(200)).all()
    return [svc.summary_out(db, c) for c in rows]


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return svc.campaign_out(db, _camp(db, cur, campaign_id))


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    c = _camp(db, cur, campaign_id)
    svc.ensure_idle(db, c)
    # the finished ads are Library images and stay there
    db.delete(c)
    db.commit()
    return Response(status_code=204)


@router.post("/{campaign_id}/continue", response_model=JobOut, status_code=202,
             summary="Approve the concepts and make the photos and ads")
def continue_campaign(campaign_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    c = _camp(db, cur, campaign_id)
    if c.concepts and not any(x.get("approved") for x in c.concepts):
        raise HTTPException(422, "Tick at least one concept to make")
    for k in svc.STAGE_KEYS:
        rec = svc.stage(c, k)
        if rec.get("status") == "failed":
            rec.update(status="pending", detail=None)
            svc.put_stage(c, k, rec)
    # concepts approved since the last run need their photos and ads
    if svc.done(c, "layout") and any(x.get("approved") and not x.get("photos") for x in c.concepts or []):
        svc.reset_from(c, "photos")
    job = svc.queue(db, c)
    db.commit()
    return job_out(job)


@router.patch("/{campaign_id}/concepts/{concept_id}", summary="Edit a concept's copy, or tick it on or off",
              description="Your edits are kept as written. When the concept's ads exist they are laid out again "
                          "on the spot (no GPU).")
def patch_concept(campaign_id: str, concept_id: str, body: ConceptPatch, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    c = _camp(db, cur, campaign_id)
    svc.ensure_idle(db, c)
    con = svc.concept(c, concept_id)
    data = body.model_dump(exclude_unset=True)
    copy_changed = False
    for k, v in data.items():
        if k == "approved":
            con["approved"] = bool(v)
            continue
        if k == "offers":
            v = [" ".join(o.split()) for o in v or [] if o and o.strip()]
        elif isinstance(v, str):
            v = "\n".join(" ".join(ln.split()) for ln in v.strip().split("\n"))
        if con.get(k) != v:
            con[k] = v
            copy_changed = True
    if copy_changed:
        con["edited"] = True
    svc.put_concept(c, con)
    if copy_changed and svc.ads_of(c, con["id"]):
        svc.mark_dirty(c, con["id"])
        svc.render_concept(db, c, svc.concept(c, con["id"]), svc.kit_of(db, c))
        svc.write_ad_json(db, c)
    svc.touch(c)
    db.commit()
    return svc.campaign_out(db, c)


@router.post("/{campaign_id}/concepts/{concept_id}/regenerate", summary="Redo one part of a concept",
             description="what=copy writes this concept's copy again (the photo stays); what=photo makes a new "
                         "photo (the copy stays); both lay the ads out again. what=layout switches the layout "
                         "style and re-renders on the spot, no GPU.")
def regenerate(campaign_id: str, concept_id: str, body: RegenerateIn, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    c = _camp(db, cur, campaign_id)
    svc.ensure_idle(db, c)
    con = svc.concept(c, concept_id)
    if body.what == "layout":
        cur_style = con.get("layout") if con.get("layout") in AD_LAYOUTS else "frosted"
        con["layout"] = body.layout or AD_LAYOUTS[(AD_LAYOUTS.index(cur_style) + 1) % len(AD_LAYOUTS)]
        svc.put_concept(c, con)
        for a in svc.ads_of(c, con["id"]):
            a["overrides"] = {k: v for k, v in (a.get("overrides") or {}).items() if k != "style"}
            a["dirty"] = True
            svc.put_ad(c, a)
        svc.render_concept(db, c, svc.concept(c, con["id"]), svc.kit_of(db, c))
        svc.write_ad_json(db, c)
        svc.touch(c)
        db.commit()
        return svc.campaign_out(db, c)
    if body.what == "copy":
        con["copy_dirty"] = True
        svc.put_concept(c, con)
        svc.reset_from(c, "concepts")
    else:
        if not con.get("approved"):
            raise HTTPException(409, "Tick this concept first")
        # the old photos stay as generations; the concept just stops pointing at them
        con.update(photos={}, error=None)
        svc.put_concept(c, con)
        svc.mark_dirty(c, con["id"])
        svc.reset_from(c, "photos")
    svc.queue(db, c)
    db.commit()
    return svc.campaign_out(db, c)


def _apply_patch(rec: dict, body: AdPatch) -> dict:
    o = {} if body.reset else dict(rec.get("overrides") or {})
    if body.texts is not None:
        t = {**(o.get("texts") or {}), **body.texts.model_dump(exclude_unset=True)}
        o["texts"] = {k: v for k, v in t.items() if v is not None}
    for k in ("style", "font_preset"):
        v = getattr(body, k)
        if v is not None:
            o[k] = v
    if body.colours is not None:
        o["colours"] = {**(o.get("colours") or {}), **body.colours}
    if body.logo is not None:
        o["logo"] = {**(o.get("logo") or {}), **body.logo.model_dump(exclude_none=True)}
    if body.footer is not None:
        o["footer"] = body.footer
    if body.footer_items is not None:
        o["footer_items"] = {**(o.get("footer_items") or {}), **body.footer_items.model_dump(exclude_none=True)}
    if body.follow is not None:
        o["follow"] = {**(o.get("follow") or {}), **body.follow.model_dump(exclude_none=True)}
    if body.focus is not None:
        o["focus"] = body.focus.model_dump()
    if body.shape is not None:
        o["shape"] = {**(o.get("shape") or {}), **body.shape.model_dump(exclude_unset=True)}
    if body.placement is not None:
        o["placement"] = body.placement.model_dump()
    return o


@router.patch("/{campaign_id}/ads/{ad_id}", summary="Edit one ad and lay it out again",
              description="Texts, layout style, font preset, colours, logo position, footer, photo focus. "
                          "Re-rendered on the CPU straight away as the next version of its Library image.")
def patch_ad(campaign_id: str, ad_id: str, body: AdPatch, db: Session = Depends(get_db),
             cur: CurrentUser = Depends(require_editor)):
    c = _camp(db, cur, campaign_id)
    svc.ensure_idle(db, c)
    rec = svc.ad(c, ad_id)
    con = svc.concept(c, rec["concept_id"])
    kit = svc.kit_of(db, c)
    _check_font_preset(body.font_preset, kit)
    rec["overrides"] = _apply_patch(rec, body)
    if svc.photo_id(con, svc.GROUPS[rec["format"]]) is None:
        raise HTTPException(409, "This ad has no photo yet")
    try:
        rec = svc.write_ad(db, c, con, rec, kit)
    except lay.LayoutError as e:
        raise HTTPException(422, str(e)) from None
    svc.write_ad_json(db, c)
    svc.touch(c)
    db.commit()
    return svc.ad_out(db, c, rec, con, kit)


@router.post("/{campaign_id}/ads/{ad_id}/preview", responses={200: {"content": {"image/png": {}}}},
             response_class=Response, summary="Preview unsaved edits",
             description="The ad with these edits applied, nothing saved; for the editor's live view.")
def preview_ad(campaign_id: str, ad_id: str, body: AdPatch, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(get_current_user)):
    c = _camp(db, cur, campaign_id)
    rec = svc.ad(c, ad_id)
    con = svc.concept(c, rec["concept_id"])
    kit = svc.kit_of(db, c)
    _check_font_preset(body.font_preset, kit)
    rec["overrides"] = _apply_patch(rec, body)
    try:
        img, _ = svc.render_spec(db, c, svc.spec_for(c, con, rec, kit), kit)
    except lay.LayoutError as e:
        raise HTTPException(422, str(e)) from None
    img.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=3)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{campaign_id}/zip", response_class=Response, responses={200: {"content": {"application/zip": {}}}},
            summary="Every finished ad and ad.json in one zip")
def download_zip(campaign_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    c = _camp(db, cur, campaign_id)
    cons = {x["id"]: x for x in c.concepts or []}
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for a in c.ads or []:
            g = db.get(Generation, a.get("generation_id") or "")
            path = generation_file(g) if g is not None else None
            if path is None or not path.is_file():
                continue
            con = cons.get(a["concept_id"]) or {}
            z.write(path, f"concept-{con.get('n', 0)}_{a['format'].replace(':', 'x')}.png")
            n += 1
        z.writestr("ad.json", json.dumps(svc.ad_json(db, c), ensure_ascii=False, indent=2))
    if not n:
        raise HTTPException(409, "No finished ads yet")
    name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (c.title or "ads"))[:60].strip("-") or "ads"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
