from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app import visibility as vis
from app import cast, character_pack, character_panels as cp, character_sheet as cs, models_catalog, voices
from app import library as lib, shot_speech, voice_instruct
from app.ai_jobs import enqueue_ai
from app.db import get_db, release
from app.models import Character, Generation, Job, MediaItem, Project, VoiceProfile, utcnow
from app.schemas import (BoardIn, CastLinkIn, CharacterCreate, CharacterFromPhoto, CharacterOut, CharacterPatch,
                         CharacterVoiceDesignIn, CharacterVoiceDesignOut, GenerationOut, JobOut, OwnImageIn,
                         PanelRegenerateIn, PhotoConsent, SheetCropsIn, SheetIn, SheetPatch, TestClipIn, TestClipOut,
                         VoiceOut)
from app.openapi import multipart
from app.security import CurrentUser, get_current_user, require_editor
from app.services import character_out, enqueue_generation, gen_out, generation_file, get_owned, job_out

router = APIRouter(tags=["characters"])

PHOTO_CONSENT = "I have the right to use this photo, and the person in it agreed to it being made into a character."
PHOTO_PROMPT = ("Picture 1 is a photo of a real person. Make a clean photorealistic head-and-shoulders reference "
                "portrait of exactly this person: keep the face, features, skin tone, hair and age exactly the same, "
                "slight three-quarter turn, neutral expression, plain neutral grey background, soft even studio "
                "light, sharp focus on the eyes.")


def _stem(name: str) -> str:
    # headers are latin-1; an Urdu name falls back to "character"
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in " -_") else "" for ch in name).strip() or "character"


def _character(db: Session, cur: CurrentUser, character_id: str) -> Character:
    return get_owned(db, Character, character_id, cur.workspace_id, "Character")


def _consent(cur: CurrentUser, consent: PhotoConsent | None) -> dict:
    name = " ".join((consent.name if consent else "").split())
    if not name:
        raise HTTPException(422, "Using a photo of a real person needs a consent record: the person's name")
    if " ".join(consent.statement.split()) != PHOTO_CONSENT:
        raise HTTPException(422, f"The consent statement must read exactly: “{PHOTO_CONSENT}”")
    return {"name": name[:120], "statement": PHOTO_CONSENT, "at": utcnow().isoformat() + "Z", "user_id": cur.id}


# ---------------------------------------------------------------- a film's cast

@router.get("/projects/{project_id}/characters", response_model=list[CharacterOut])
def list_characters(project_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    get_owned(db, Project, project_id, cur.workspace_id, "Project")
    return [character_out(db, c) for c in cast.project_cast(db, project_id)]


@router.post("/projects/{project_id}/characters", response_model=CharacterOut, status_code=201)
def create_character(
    project_id: str, body: CharacterCreate, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)
):
    get_owned(db, Project, project_id, cur.workspace_id, "Project")
    c = Character(
        workspace_id=cur.workspace_id,
        project_id=project_id,
        name=body.name,
        description=body.description,
        source="user",
        locked=bool(body.description),
        profile=cs.clean_profile({}, body.profile),
    )
    db.add(c)
    db.commit()
    return character_out(db, c)


@router.post("/projects/{project_id}/cast", response_model=CharacterOut, status_code=201)
def link_character(project_id: str, body: CastLinkIn, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_editor)):
    """Cast a Character Studio character in this film; it stays one character, shared."""
    p = get_owned(db, Project, project_id, cur.workspace_id, "Project")
    c = _character(db, cur, body.character_id)
    cast.link(db, p, c)
    p.updated_at = utcnow()
    db.commit()
    return character_out(db, c)


@router.delete("/projects/{project_id}/cast/{character_id}", status_code=204)
def unlink_character(project_id: str, character_id: str, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    get_owned(db, Project, project_id, cur.workspace_id, "Project")
    if not cast.unlink(db, project_id, _character(db, cur, character_id)):
        raise HTTPException(404, "That character isn't linked into this film")
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- Character Studio

@router.get("/characters", response_model=list[CharacterOut])
def list_studio(q: str | None = Query(None, max_length=120), scope: str = Query("studio", pattern="^(studio|all)$"),
                db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    """The studio library; scope=all adds every film's own characters (for pickers)."""
    stmt = select(Character).where(Character.workspace_id == cur.workspace_id, *vis.where(Character, vis.wanted(cur)))
    if scope == "studio":
        stmt = stmt.where(Character.project_id.is_(None))
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Character.name.ilike(like), Character.description.ilike(like)))
    rows = db.scalars(stmt.order_by(Character.updated_at.desc()).limit(300)).all()
    return [character_out(db, c, films=True) for c in rows]


@router.post("/characters", response_model=CharacterOut, status_code=201)
def create_studio_character(body: CharacterCreate, db: Session = Depends(get_db),
                            cur: CurrentUser = Depends(require_editor)):
    c = Character(workspace_id=cur.workspace_id, project_id=None, name=body.name.strip(),
                  description=body.description.strip(), source="user", locked=bool(body.description.strip()),
                  profile=cs.clean_profile({}, body.profile))
    db.add(c)
    db.commit()
    return character_out(db, c, films=True)


@router.post("/characters/from-photo", response_model=CharacterOut, status_code=201)
def create_from_photo(body: CharacterFromPhoto, db: Session = Depends(get_db),
                      cur: CurrentUser = Depends(require_editor)):
    """A studio character from an uploaded photo (consent first): a clean studio edit of it, or with
    use=as_is the photo itself as the approved portrait."""
    consent = _consent(cur, body.consent)
    item, photo = cs.uploaded_image(db, cur.workspace_id, body.media_id, "photo")
    pic = cs.open_picture(photo) if body.use == "as_is" else None
    c = Character(workspace_id=cur.workspace_id, project_id=None, name=body.name.strip(),
                  description=body.description.strip(), source="user", locked=bool(body.description.strip()),
                  sheet={"photo": {"media_id": item.id, "generation_id": photo.id, "use": body.use,
                                   "consent": consent}})
    db.add(c)
    db.flush()
    if pic is not None:
        cs.add_own(db, c, pic, params={"from_photo": item.id, "consent": consent})
        db.commit()
        return character_out(db, c, films=True)
    params = models_catalog.apply_image_choice({"reference_ids": [photo.id], "from_photo": item.id}, None)
    enqueue_generation(db, workspace_id=cur.workspace_id, project_id=None, target_type="character", target_id=c.id,
                       kind="portrait", prompt=PHOTO_PROMPT, params=params)
    db.commit()
    return character_out(db, c, films=True)


@router.post("/characters/import", response_model=CharacterOut, status_code=201,
             openapi_extra=multipart(file_help="A character pack .zip exported from Character Studio"))
async def import_pack(request: Request, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    c = await character_pack.receive_and_import(request, db, cur)
    return character_out(db, c, films=True)


@router.get("/character-sheet/catalogue")
def sheet_catalogue(cur: CurrentUser = Depends(get_current_user)):
    return cp.catalogue_out()


@router.get("/characters/{character_id}", response_model=CharacterOut)
def get_character(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return character_out(db, _character(db, cur, character_id), films=True)


@router.patch("/characters/{character_id}", response_model=CharacterOut)
def patch_character(
    character_id: str, body: CharacterPatch, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)
):
    c = _character(db, cur, character_id)
    data = body.model_dump(exclude_unset=True)
    if "profile" in data:
        c.profile = cs.clean_profile(c.profile, data.pop("profile"))
    if "voice_id" in data:
        vid = (data.pop("voice_id") or "").strip()
        if vid and voices.find_voice(db, cur.workspace_id, vid) is None:
            raise HTTPException(422, "That voice isn't in your library or the gallery")
        c.voice_id = vid or None
    if "performance" in data:
        perf = data.pop("performance") or {}
        c.performance = {k: v for k, v in {"expressiveness": perf.get("expressiveness"),
                                            "gestures": " ".join((perf.get("gestures") or "").split())}.items() if v}
    changes = {k: v for k, v in data.items() if v is not None}
    text_changed = any(changes.get(f, getattr(c, f)) != getattr(c, f) for f in ("name", "description"))
    for k, v in changes.items():
        setattr(c, k, v)
    if text_changed:
        # same provenance rule as scenes
        c.source = "ai_edited" if c.source in ("ai", "ai_edited") else "user"
        c.locked = True
    c.updated_at = utcnow()
    db.commit()
    return character_out(db, c, films=True)


@router.post("/characters/{character_id}/save-to-studio", response_model=CharacterOut)
def save_to_studio(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """A film's own character moves into the Character Studio and stays cast in that film."""
    c = _character(db, cur, character_id)
    cast.save_to_studio(db, c)
    db.commit()
    return character_out(db, c, films=True)


@router.delete("/characters/{character_id}", status_code=204)
def delete_character(character_id: str, project_id: str | None = None, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    """From a film (project_id given) a studio character is only unlinked. Otherwise it goes everywhere;
    GET /characters/{id} lists the films first so the UI can name them."""
    c = _character(db, cur, character_id)
    if project_id and c.project_id != project_id:
        if not cast.unlink(db, project_id, c):
            raise HTTPException(404, "That character isn't in this film")
        db.commit()
        return Response(status_code=204)
    cast.remove_everywhere(db, c)
    db.execute(delete(Generation).where(Generation.target_type == "character", Generation.target_id == c.id))
    db.delete(c)
    db.commit()
    return Response(status_code=204)


@router.post("/characters/{character_id}/portrait/upload", response_model=GenerationOut, status_code=201)
def upload_portrait(character_id: str, body: OwnImageIn, db: Session = Depends(get_db),
                    cur: CurrentUser = Depends(require_editor)):
    """The user's own picture as a new portrait version; approved straight away only if nothing is yet."""
    c = _character(db, cur, character_id)
    params = {"consent": _consent(cur, body.consent)} if body.consent else {}
    item, g = cs.uploaded_image(db, cur.workspace_id, body.media_id)
    first = db.scalars(select(Generation.id).where(
        Generation.target_type == "character", Generation.target_id == c.id, Generation.kind == "portrait",
        Generation.status == "approved")).first() is None
    out = cs.add_own(db, c, cs.open_picture(g), params={**params, "from_media": item.id}, approved=first)
    c.updated_at = utcnow()
    db.commit()
    return gen_out(out)


@router.post("/characters/{character_id}/ai/profile", response_model=JobOut, status_code=202)
def ai_profile(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    """Fill the empty profile fields (and suggest props, accessories, wardrobe details) from the description."""
    c = _character(db, cur, character_id)
    running = db.scalars(select(Job).where(Job.type == "ai_character_profile", Job.workspace_id == cur.workspace_id,
                                           Job.status.in_(("queued", "running")))).all()
    hit = next((j for j in running if (j.payload or {}).get("character_id") == c.id), None)
    if hit is None:
        hit = enqueue_ai(db, workspace_id=cur.workspace_id, type="ai_character_profile",
                         payload={"character_id": c.id}, project_id=c.project_id)
        db.commit()
    return job_out(hit)


# ---------------------------------------------------------------- the sheet

@router.get("/characters/{character_id}/sheet")
def get_sheet(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return cs.sheet_out(db, _character(db, cur, character_id))


@router.post("/characters/{character_id}/sheet", status_code=202)
def make_sheet(character_id: str, body: SheetIn, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    lists = body.lists if body.lists is not None else (c.sheet or {}).get("lists")
    queued = cs.start(db, c, body.preset, counts=body.counts, lists=lists, backdrop=body.backdrop)
    db.commit()
    return {**cs.sheet_out(db, c), "queued": len(queued)}


@router.patch("/characters/{character_id}/sheet")
def patch_sheet(character_id: str, body: SheetPatch, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    cs.update_text(c, titles=body.titles, labels=body.labels, footer=body.footer, ruler=body.ruler,
                   backdrop=body.backdrop)
    if body.lists is not None:
        cs.put(c, lists={**((c.sheet or {}).get("lists") or {}), **cs.clean_lists(body.lists)})
    if (c.sheet or {}).get("board") and (body.titles, body.labels, body.footer, body.ruler) != (None,) * 4:
        cs.queue_board(db, c)
    db.commit()
    return cs.sheet_out(db, c)


@router.post("/characters/{character_id}/sheet/panels/{key}/regenerate", response_model=GenerationOut,
             status_code=201)
def regenerate_panel(character_id: str, key: str, body: PanelRegenerateIn | None = None,
                     db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    g = cs.regenerate(db, c, key, body.note if body else "")
    db.commit()
    return gen_out(g)


@router.post("/characters/{character_id}/sheet/panels/{key}/upload", response_model=GenerationOut,
             status_code=201)
def upload_panel(character_id: str, key: str, body: OwnImageIn, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    """The user's own picture as this panel's approved version."""
    c = _character(db, cur, character_id)
    if key == "portrait" or cp.panel(key, cs.extras(c)) is None:
        raise HTTPException(404, f"This sheet has no panel '{key}'")
    params = {"consent": _consent(cur, body.consent)} if body.consent else {}
    item, g = cs.uploaded_image(db, cur.workspace_id, body.media_id)
    out = cs.add_own(db, c, cs.open_picture(g), panel=key, params={**params, "from_media": item.id})
    db.commit()
    return gen_out(out)


@router.post("/characters/{character_id}/sheet/upload")
def upload_sheet(character_id: str, body: OwnImageIn, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    """The user's own character sheet becomes the board as it is; panels can then be cut from it."""
    c = _character(db, cur, character_id)
    consent = _consent(cur, body.consent) if body.consent else None
    item, _g = cs.uploaded_image(db, cur.workspace_id, body.media_id, "sheet")
    cs.use_own_sheet(db, c, item)
    if consent:
        cs.put(c, own_sheet={**c.sheet["own_sheet"], "consent": consent})
    c.updated_at = utcnow()
    db.commit()
    return cs.sheet_out(db, c)


@router.post("/characters/{character_id}/sheet/crops")
def cut_sheet(character_id: str, body: SheetCropsIn, db: Session = Depends(get_db),
              cur: CurrentUser = Depends(require_editor)):
    c = _character(db, cur, character_id)
    made = cs.cut_panels(db, c, [x.model_dump() for x in body.crops])
    db.commit()
    return {**cs.sheet_out(db, c), "saved": len(made)}


@router.post("/characters/{character_id}/sheet/board", response_model=JobOut, status_code=202)
def make_board(character_id: str, body: BoardIn, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    """Lays out a board from the panels; this is also how an uploaded sheet gets replaced."""
    c = _character(db, cur, character_id)
    if not any(cs.current(v) for v in cs.panel_gens(db, c.id).values()):
        raise HTTPException(409, "No sheet panels are finished yet")
    job = cs.queue_board(db, c, body.layout, replace_upload=True)
    db.commit()
    return job_out(job)


@router.get("/characters/{character_id}/sheet/board/download")
def download_board(character_id: str, format: str = Query("png", pattern="^(png|pdf)$"),
                   db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    c = _character(db, cur, character_id)
    item = db.get(MediaItem, ((c.sheet or {}).get("board") or {}).get("media_id") or "")
    g = db.get(Generation, item.generation_id) if item is not None and item.generation_id else None
    path = generation_file(g) if g is not None and g.status in cs.FINISHED else None
    if path is None or not path.is_file():
        raise HTTPException(404, "This sheet has no board yet")
    stem = _stem(c.name)
    headers = {"Cache-Control": "private, no-store"}
    if format == "pdf":
        headers["Content-Disposition"] = f'attachment; filename="{stem} sheet.pdf"'
        return Response(cs.board_pdf(path), media_type="application/pdf", headers=headers)
    # an uploaded sheet goes back as the file it came in as
    release(db)
    return FileResponse(path, media_type=g.media_type or "image/png", filename=f"{stem} sheet{path.suffix.lower()}",
                        headers=headers)


@router.get("/characters/{character_id}/pack")
def export_pack(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    c = _character(db, cur, character_id)
    data = character_pack.build(db, c)
    stem = _stem(c.name)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.mixchar.zip"',
                             "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- voice (contract v26)

def _profile_language(profile: dict) -> str:
    # "Urdu, English" speaks Urdu first; nothing recognisable falls back to English
    text = str((profile or {}).get("languages") or "").casefold()
    found = [(text.find(name.casefold()), code) for code, name in voices.LANG_NAMES.items()
             if name.casefold() in text]
    return min(found)[1] if found else "en"


@router.post("/characters/{character_id}/voice/design", response_model=CharacterVoiceDesignOut, status_code=201)
def design_voice(character_id: str, body: CharacterVoiceDesignIn | None = None, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    """A designed OmniVoice voice from the profile's gender, age and accent, saved to Voices and given to the
    character."""
    body = body or CharacterVoiceDesignIn()
    c = _character(db, cur, character_id)
    raw, ins = voice_instruct.from_profile(c.profile, body.extra)
    if not ins.text:
        raise HTTPException(422, "The profile doesn't say enough about the voice yet. Add a gender, an age or an "
                                 "accent on the Profile tab, or describe it in the box.")
    gender = next((g for g in ("male", "female") if g in ins.understood), None)
    v = VoiceProfile(workspace_id=cur.workspace_id, name=(body.name or f"{c.name}'s voice").strip()[:120],
                     kind="design", language=(body.language or _profile_language(c.profile)).lower(),
                     notes=f"Designed for {c.name} from their profile: {raw}"[:2000], gender_hint=gender, uses=0,
                     design=voices.clean_design({"description": ins.text, "engine": voices.default_engine()}))
    db.add(v)
    db.flush()
    c.voice_id = v.id
    c.updated_at = utcnow()
    db.commit()
    return CharacterVoiceDesignOut(character=character_out(db, c, films=True), voice=VoiceOut(**voices.voice_out(db, v)),
                                   description=raw, understood=ins.understood, ignored=ins.ignored)


def _test_clip_out(db: Session, c: Character, **extra) -> TestClipOut:
    rec = (c.sheet or {}).get("test_clip") or {}
    item = db.get(MediaItem, rec.get("media_id") or "")
    if item is None or item.workspace_id != c.workspace_id:
        return TestClipOut()
    return TestClipOut(item=lib.item_out(db, item), text=rec.get("text") or "", panel=rec.get("panel"), **extra)


@router.get("/characters/{character_id}/test-clip", response_model=TestClipOut)
def get_test_clip(character_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return _test_clip_out(db, _character(db, cur, character_id))


@router.post("/characters/{character_id}/test-clip", response_model=TestClipOut, status_code=202)
def make_test_clip(character_id: str, body: TestClipIn, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_editor)):
    """A 5-10 s lip-synced clip of the character saying the line, from the close-up panel (or the portrait)."""
    c = _character(db, cur, character_id)
    out = shot_speech.queue_test_clip(db, cur, c, body.text, quality=body.quality, finish=body.finish,
                                    engine=body.lipsync_engine)
    db.commit()
    return _test_clip_out(db, c, job=out["job"], speech_jobs=out["speech_jobs"])
