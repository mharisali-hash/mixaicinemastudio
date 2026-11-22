"""My preview: the patient's own view of her Implant Preview record, linked by the code her surgeon gave her.

Read-only apart from trying sizes on the clinic's photos. Not behind require_implant (patients don't have the
studio), so every route here answers only for the one record linked to the signed-in account."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.implant import OptionIn
from app.config import get_settings
from app.db import get_db, release
from app.implant import service as im
from app.models import ACTING_USER, Generation, ImplantPatient, ImplantSession, User
from app.quota import QUOTA_USER
from app.ratelimit import SlidingWindow
from app.security import CurrentUser, get_current_user, session_only

router = APIRouter(prefix="/implant-patient", tags=["implant"])

# wrong codes per account: 8 letters from 31 is plenty, this only stops a script working through them
LINK_FAILURES = SlidingWindow(limit=10, window_s=15 * 60)
TRIES_PER_DAY = SlidingWindow(limit=20, window_s=24 * 3600)
MAX_BUSY_TRIES = 3
# inline only, never cached: the page shows them, nothing offers them as a download
VIEW_HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
                "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"}
BUSY = ("queued", "generating")


class LinkIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class TryIn(BaseModel):
    option: OptionIn


def _mine(db: Session, cur: CurrentUser) -> ImplantPatient:
    p = im.linked_patient(db, cur.user.id)
    if p is None:
        raise HTTPException(404, "No clinic record is linked to your account")
    return p


def _url(g: Generation) -> str | None:
    return f"/api/implant-patient/files/{g.id}" if g.file_path and g.status in ("ready", "approved") else None


def _option(o: dict) -> dict:
    return {k: o.get(k) for k in ("label", "left_cc", "right_cc", "profile", "shape", "placement", "fill", "procedure")}


def _me_out(db: Session, p: ImplantPatient) -> dict:
    # no measurements, notes, consent details or chest boxes: those are the surgeon's working file
    surgeon = db.get(User, p.surgeon_user_id)
    sessions = list(db.scalars(select(ImplantSession).where(ImplantSession.patient_id == p.id)
                               .order_by(ImplantSession.created_at.desc())))
    chart = p.chart or {}
    approved = next((s for s in sessions if s.id == chart.get("session_id")), None)
    usable = im.photos(db, p).items() if not im.is_minor(p) else []
    return {
        "linked": True,
        "label": p.label,
        "clinic": (surgeon.display_name or "Your clinic") if surgeon else "Your clinic",
        "kept_until": p.last_activity_at + im.keep_for(p),
        "photos": [{"id": g.id, "view": v, "view_label": im.VIEWS[v], "url": _url(g)}
                   for v, g in usable if not (g.params or {}).get("age_flag")],
        "sessions": [{"id": s.id, "created_at": s.created_at,
                      "by": "you" if any(o.get("by") == "patient" for o in s.options) else "clinic",
                      "options": [_option(o) for o in s.options],
                      "results": [{"id": g.id, "status": g.status, "url": _url(g),
                                   "option": g.params["implant"]["option"], "view": g.params["implant"]["view"]}
                                  for g in im.results(db, s)]} for s in sessions],
        "approved": {"option": _option(approved.options[chart["option"]]), "approved_at": chart.get("approved_at")}
        if approved and chart.get("option") is not None and chart["option"] < len(approved.options) else None,
        "can_try": not im.is_minor(p) and bool(usable),
    }


@router.get("/me")
@session_only
def me(poll: bool = False, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    p = im.linked_patient(db, cur.user.id)
    if p is None:
        return {"linked": False}
    # the page refreshes every few seconds while a preview renders; one row per visit, not per refresh
    if not poll:
        audit.record(db, cur.user, "implant_patient_self_view", "implant_patient", p.id)
        db.commit()
    return _me_out(db, p)


@router.post("/link")
@session_only
def link(body: LinkIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    key = cur.user.id
    wait = LINK_FAILURES.retry_after(key)
    if wait:
        raise HTTPException(429, f"Too many wrong codes. Try again in {wait} seconds.",
                            headers={"Retry-After": str(wait)})
    p = im.find_invite(db, body.code)
    if p is None or p.patient_user_id:
        LINK_FAILURES.hit(key)
        raise HTTPException(422, "That code isn't right, or it has expired. Ask your clinic for a new one.")
    if p.surgeon_user_id == cur.user.id:
        raise HTTPException(409, "This is your own patient's code: give it to the patient")
    other = im.linked_patient(db, cur.user.id)
    if other is not None and other.id != p.id:
        raise HTTPException(409, "Your account is already linked to a clinic record")
    p.patient_user_id = cur.user.id
    # single use
    p.invite_code_hash = None
    p.invite_expires_at = None
    im.touch(p)
    audit.record(db, cur.user, "implant_patient_link", "implant_patient", p.id)
    db.commit()
    return _me_out(db, p)


@router.post("/me/try", status_code=202)
@session_only
def try_size(body: TryIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    """A new preview on the clinic's own photos. The surgeon sees it in the record like any other."""
    p = _mine(db, cur)
    sids = select(ImplantSession.id).where(ImplantSession.patient_id == p.id)
    busy = db.scalar(select(func.count(func.distinct(Generation.target_id))).where(
        Generation.target_type == "implant_result", Generation.target_id.in_(sids), Generation.status.in_(BUSY)))
    if busy >= MAX_BUSY_TRIES:
        raise HTTPException(429, f"{busy} previews are still on the way. Try another when one has finished.")
    wait = TRIES_PER_DAY.retry_after(cur.user.id)
    if wait:
        raise HTTPException(429, "That's enough previews for today. Try again tomorrow.",
                            headers={"Retry-After": str(wait)})
    opt = {**body.option.model_dump(), "by": "patient"}
    # the surgeon's results list shows labels; the patient's page doesn't
    opt["label"] = opt["label"] or "Patient's try"
    # the clinic's record, the clinic's work: the job carries the surgeon and counts against the clinic's plan
    db.info[ACTING_USER] = p.surgeon_user_id
    db.info[QUOTA_USER] = p.surgeon_user_id
    s = im.generate(db, p, [opt])
    TRIES_PER_DAY.hit(cur.user.id)
    audit.record(db, cur.user, "implant_patient_try", "implant_patient", p.id, session=s.id,
                 left_cc=opt["left_cc"], right_cc=opt["right_cc"])
    db.commit()
    return _me_out(db, p)


@router.get("/files/{gen_id}")
@session_only
def view_file(gen_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    p = _mine(db, cur)
    g = db.get(Generation, gen_id)
    if g is None or not g.file_path or g.target_type not in ("implant_photo", "implant_result"):
        raise HTTPException(404, "Not found")
    if g.target_type == "implant_photo":
        if g.target_id != p.id or (g.params or {}).get("age_flag") or im.is_minor(p):
            raise HTTPException(404, "Not found")
    else:
        s = db.get(ImplantSession, g.target_id)
        if s is None or s.patient_id != p.id or g.status not in ("ready", "approved"):
            raise HTTPException(404, "Not found")
    root = (get_settings().data_dir / "clinic").resolve()
    path = (get_settings().data_dir / g.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Not found")
    photo = g.target_type == "implant_photo"
    release(db)
    if photo:
        return Response(im.marked_png(path), media_type="image/png", headers=VIEW_HEADERS)
    # results carry the SIMULATION mark already (service.stamp)
    return FileResponse(path, media_type=g.media_type or "image/png", headers=VIEW_HEADERS)
