"""Character Looks: a character's outfits, settings and poses, each with its own picture."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import character_looks as cl
from app import character_sheet as cs
from app.api.characters import _consent
from app.db import get_db
from app.models import Character, CharacterLook, Generation, utcnow
from app.schemas import PhotoConsent
from app.security import CurrentUser, get_current_user, require_editor
from app.services import get_owned, next_version

router = APIRouter(tags=["characters"])

Text = Field("", max_length=600)


class LookIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    outfit: str = Text
    setting: str = Text
    pose: str = Text
    prompt: str = Field("", max_length=1000, description="Anything else about this look")
    make: bool = Field(True, description="Queue its picture straight away")


class LookPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    outfit: str | None = Field(None, max_length=600)
    setting: str | None = Field(None, max_length=600)
    pose: str | None = Field(None, max_length=600)
    prompt: str | None = Field(None, max_length=1000)
    is_default: bool | None = None
    sort: int | None = Field(None, ge=0, le=1000)


class PackIn(BaseModel):
    pack: Literal["business", "casual", "traditional", "presenter", "outdoor"]


class RedoIn(BaseModel):
    note: str = Field("", max_length=500, description="What to change this time")


class OwnLookIn(BaseModel):
    media_id: str
    # a photo of a real person needs the same consent as "From a photo"
    consent: PhotoConsent


def _character(db: Session, cur: CurrentUser, cid: str) -> Character:
    return get_owned(db, Character, cid, cur.workspace_id, "Character")


def _look(db: Session, c: Character, lid: str) -> CharacterLook:
    look = db.get(CharacterLook, lid)
    if look is None or look.character_id != c.id:
        raise HTTPException(404, "Look not found")
    return look


@router.get("/characters/{character_id}/looks")
def list_looks(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    c = _character(db, cur, character_id)
    return {"looks": [cl.look_out(db, x) for x in cl.looks_of(db, c)],
            "packs": [{"id": k, "label": v["label"], "looks": [x[0] for x in v["looks"]]} for k, v in cl.PACKS.items()]}


@router.post("/characters/{character_id}/looks", status_code=201)
def add_look(character_id: str, body: LookIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    look = cl.add(db, c, **body.model_dump())
    db.commit()
    return cl.look_out(db, look)


@router.post("/characters/{character_id}/looks/pack", status_code=201)
def add_pack(character_id: str, body: PackIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    looks = cl.add_pack(db, c, body.pack)
    db.commit()
    return {"looks": [cl.look_out(db, x) for x in looks]}


@router.patch("/characters/{character_id}/looks/{look_id}")
def patch_look(character_id: str, look_id: str, body: LookPatch, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    look = _look(db, c, look_id)
    data = body.model_dump(exclude_unset=True)
    if data.pop("is_default", None):
        cl.set_default(db, c, look)
    for k, v in data.items():
        if v is not None:
            setattr(look, k, v.strip() if isinstance(v, str) else v)
    look.updated_at = utcnow()
    db.commit()
    return cl.look_out(db, look)


@router.delete("/characters/{character_id}/looks/{look_id}", status_code=204)
def delete_look(character_id: str, look_id: str, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    cl.remove(db, c, _look(db, c, look_id))
    db.commit()


@router.post("/characters/{character_id}/looks/{look_id}/generate", status_code=201)
def generate_look(character_id: str, look_id: str, body: RedoIn | None = None, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    look = _look(db, c, look_id)
    cl.queue_picture(db, c, look, (body.note if body else "") or "")
    db.commit()
    return cl.look_out(db, look)


@router.post("/characters/{character_id}/looks/{look_id}/upload", status_code=201)
def upload_look(character_id: str, look_id: str, body: OwnLookIn, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    """The user's own photo as this look's picture, used as it is (approved)."""
    c = _character(db, cur, character_id)
    look = _look(db, c, look_id)
    consent = _consent(cur, body.consent)
    item, photo = cs.uploaded_image(db, cur.workspace_id, body.media_id, "photo")
    # one approved picture per look (uq_generation_one_approved): the old one steps down first
    for old in db.query(Generation).filter(Generation.target_type == cl.TARGET, Generation.target_id == look.id,
                                           Generation.status == "approved"):
        old.status, old.approved_at = "ready", None
    db.flush()
    g = cs.store_picture(c, cs.open_picture(photo), cl.KIND, prompt=cs.UPLOAD_PROMPT, status="approved",
                         params={"source": "upload", "from_photo": item.id, "consent": consent},
                         version=next_version(db, cl.TARGET, look.id, cl.KIND))
    g.target_type, g.target_id = cl.TARGET, look.id
    db.add(g)
    db.commit()
    return cl.look_out(db, look)
