"""Magic prompt (UI polish P1): POST /prompts/enhance, so the dock can show and edit the result first."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.limits import PROMPT_MAX
from app import brand
from app import library as lib
from app import prompt_enhance
from app.db import get_db
from app.security import CurrentUser, require_editor

router = APIRouter(prefix="/prompts", tags=["prompts"])


class EnhanceIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX)
    kind: Literal["image", "video", "quick", "edit"] = "image"
    model: str | None = Field(None, max_length=60)
    style: str | None = Field(None, max_length=40)
    brand_kit_id: str | None = None
    mode: Literal["auto", "on"] = "auto"


class EnhanceOut(BaseModel):
    enhanced: str
    changed: bool
    notes: str | None = None


@router.post("/enhance", response_model=EnhanceOut)
async def enhance(body: EnhanceIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    if not body.prompt.strip():
        raise HTTPException(422, "A prompt is required")
    context = ""
    if body.brand_kit_id:
        kit = brand.get_kit(db, cur.workspace_id, body.brand_kit_id)
        if kit is None:
            raise HTTPException(404, "Brand kit not found")
        context = brand.prompt_context(kit)
    # image styles go by their label; quick styles (cinematic, documentary...) are labels already
    style = lib.STYLES[body.style].label if body.style in lib.STYLES else body.style
    return await prompt_enhance.enhance(body.prompt, body.kind, mode=body.mode, model=body.model, style=style,
                                        brand_context=context, modest=not cur.user.is_admin)
