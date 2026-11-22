"""Implant Preview (M22): surgeon-only. Every route answers 404 to accounts without access, and a patient is only
ever visible to the surgeon who created it."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db, release
from app.implant import planning
from app.implant import service as im
from app.implant import sheets
from app.implant.finish import clean_box
from app.implant.guard import require_implant
from app.models import Generation, ImplantPatient, ImplantSession, User, utcnow
from app.security import CurrentUser, session_only

router = APIRouter(prefix="/implant", tags=["implant"], dependencies=[Depends(require_implant)])

Profile = Literal["low", "moderate", "moderate_plus", "high", "extra_high"]
Shape = Literal["round", "teardrop"]
Placement = Literal["subglandular", "submuscular", "dual_plane"]
Fill = Literal["silicone", "saline"]
Procedure = Literal["augmentation", "augmentation_lift"]
FILE_HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


class Measurements(BaseModel):
    height_cm: float | None = Field(None, ge=100, le=230)
    weight_kg: float | None = Field(None, ge=30, le=250)
    bra_size: str = Field("", max_length=20)
    chest_width_cm: float | None = Field(None, ge=15, le=60)
    bw_left_cm: float | None = Field(None, ge=5, le=25)
    bw_right_cm: float | None = Field(None, ge=5, le=25)
    snn_left_cm: float | None = Field(None, ge=10, le=40)
    snn_right_cm: float | None = Field(None, ge=10, le=40)
    nimf_left_cm: float | None = Field(None, ge=2, le=20)
    nimf_right_cm: float | None = Field(None, ge=2, le=20)
    pinch_cm: float | None = Field(None, ge=0, le=10)
    ptosis_grade: int | None = Field(None, ge=0, le=3)
    asymmetry: str = Field("", max_length=300)


class GuardianIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relation: str = Field("", max_length=60)
    consented: bool = Field(description="The guardian signed the consent form")


def _dob_ok(dob: date | None) -> date | None:
    if dob is not None and (why := im.dob_problem(dob)):
        raise ValueError(why)
    return dob


class PatientIn(BaseModel):
    label: str = Field(min_length=1, max_length=120, description="A code or name the surgeon recognises")
    date_of_birth: date
    # older clients still send the tick; the date of birth decides now
    adult_confirmed: bool | None = None
    consent_attested: bool = Field(description="The patient signed the consent form")
    guardian: GuardianIn | None = None
    topless_ok: bool = False
    # 0 is session only: gone when the consultation ends (app.implant.service.keep_for)
    retention_months: int = Field(24, ge=0, le=120)
    measurements: Measurements = Measurements()
    notes: str = Field("", max_length=4000)

    @model_validator(mode="after")
    def _required(self):
        _dob_ok(self.date_of_birth)
        if not self.consent_attested:
            raise ValueError("Record the patient's signed consent")
        if im.years(self.date_of_birth) < im.ADULT_AGE and not (self.guardian and self.guardian.consented):
            raise ValueError("For a patient under 18, record a parent or guardian's name and signed consent")
        return self


class PatientPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=120)
    date_of_birth: date | None = None
    guardian: GuardianIn | None = None
    topless_ok: bool | None = None
    retention_months: int | None = Field(None, ge=0, le=120)
    measurements: Measurements | None = None
    notes: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def _sane(self):
        _dob_ok(self.date_of_birth)
        if self.guardian and not self.guardian.consented:
            raise ValueError("Record the guardian's signed consent")
        return self


class ConfirmAgeIn(BaseModel):
    date_of_birth: date


class InviteIn(BaseModel):
    retention_months: Literal[1, 3, 6] | None = None


class OptionIn(BaseModel):
    label: str = Field("", max_length=60)
    left_cc: int = Field(ge=100, le=900)
    right_cc: int = Field(ge=100, le=900)
    profile: Profile = "moderate_plus"
    shape: Shape = "round"
    placement: Placement = "dual_plane"
    fill: Fill = "silicone"
    procedure: Procedure = "augmentation"


class SessionIn(BaseModel):
    options: list[OptionIn] = Field(min_length=1, max_length=3)
    # the AI edit, the shape warp, or both side by side
    engines: list[Literal["ai", "warp"]] = Field(["ai"], min_length=1, max_length=2)


class ChestBoxIn(BaseModel):
    chest_box: list[int] = Field(min_length=4, max_length=4, description="[y1, x1, y2, x2] on 0-1000")


class ApproveIn(BaseModel):
    option: int = Field(ge=0, le=2)
    engine: Literal["ai", "warp"] = "ai"
    notes: str = Field("", max_length=1500)


def _gen_out(g: Generation | None) -> dict | None:
    if g is None:
        return None
    return {"id": g.id, "status": g.status, "url": f"/api/implant/files/{g.id}" if g.file_path else None,
            "job_id": g.job_id,
            **{k: v for k, v in (g.params or {}).items()
               if k in ("view", "garment", "chest_box", "garment_check", "age_flag")},
            **({"option": g.params["implant"]["option"], "view": g.params["implant"]["view"],
                "engine": im.engine_of(g)} if (g.params or {}).get("implant") else {})}


def _guardian(c: dict) -> dict | None:
    g = c.get("guardian")
    return {k: g.get(k) for k in ("name", "relation", "consented_at")} if g else None


def _patient_out(db: Session, p: ImplantPatient, full: bool = False) -> dict:
    c = p.consent or {}
    out = {"id": p.id, "label": p.label, "adult_confirmed": p.adult_confirmed,
           "date_of_birth": p.date_of_birth, "age": im.age_of(p), "minor": im.is_minor(p),
           "consent": {"at": c.get("at"), "attested_by": c.get("attested_by"), "topless_ok": bool(c.get("topless_ok")),
                       "form": bool(c.get("form_path")), "guardian": _guardian(c)},
           "retention_months": p.retention_months, "last_activity_at": p.last_activity_at, "created_at": p.created_at,
           "approved": bool((p.chart or {}).get("session_id")), "patient_linked": bool(p.patient_user_id)}
    if not full:
        return out
    linked = db.get(User, p.patient_user_id) if p.patient_user_id else None
    out["patient_account"] = {
        "linked": linked is not None,
        "name": (linked.display_name or linked.email) if linked else None,
        "invite_expires_at": p.invite_expires_at if p.invite_code_hash and p.invite_expires_at
        and p.invite_expires_at > utcnow() else None,
    }
    sessions = list(db.scalars(select(ImplantSession).where(ImplantSession.patient_id == p.id)
                               .order_by(ImplantSession.created_at.desc())))
    chart = dict(p.chart or {})
    return {**out, "measurements": p.measurements or {}, "notes": p.notes,
            "views": im.VIEWS, "photos": {v: _gen_out(g) for v, g in im.photos(db, p).items()},
            "sessions": [{"id": s.id, "created_at": s.created_at, "options": s.options,
                          "results": [_gen_out(g) for g in im.results(db, s)]} for s in sessions],
            "chart": {"session_id": chart.get("session_id"), "option": chart.get("option"),
                      "engine": chart.get("engine", "ai"),
                      "approved_at": chart.get("approved_at"), "versions": len(chart.get("versions") or []),
                      "url": f"/api/implant/patients/{p.id}/chart.pdf" if chart.get("chart_path") else None}}


def _patient(db: Session, cur: CurrentUser, pid: str) -> ImplantPatient:
    return im.own_patient(db, cur.user.id, pid)


def _session(db: Session, p: ImplantPatient, sid: str) -> ImplantSession:
    s = db.get(ImplantSession, sid)
    if s is None or s.patient_id != p.id:
        raise HTTPException(404, "Consultation not found")
    return s


@router.get("/patients")
@session_only
def list_patients(db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    rows = db.scalars(select(ImplantPatient).where(ImplantPatient.surgeon_user_id == cur.user.id)
                      .order_by(ImplantPatient.last_activity_at.desc()))
    return [_patient_out(db, p) for p in rows]


@router.post("/patients", status_code=201)
@session_only
def create_patient(body: PatientIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    adult = im.years(body.date_of_birth) >= im.ADULT_AGE
    by = cur.user.display_name or cur.user.email
    consent = {"at": utcnow().isoformat(), "attested_by": by, "topless_ok": body.topless_ok and adult}
    if body.guardian:
        consent["guardian"] = _guardian_row(body.guardian, by)
    p = ImplantPatient(workspace_id=cur.workspace_id, surgeon_user_id=cur.user.id, label=body.label.strip(),
                       date_of_birth=body.date_of_birth, adult_confirmed=adult,
                       retention_months=body.retention_months, notes=body.notes,
                       measurements=body.measurements.model_dump(exclude_none=True), consent=consent)
    db.add(p)
    db.flush()
    audit.record(db, cur.user, "implant_patient_create", "implant_patient", p.id, topless_ok=consent["topless_ok"],
                 minor=not adult, guardian=bool(body.guardian))
    db.commit()
    return _patient_out(db, p, full=True)


@router.get("/patients/{patient_id}")
@session_only
def get_patient(patient_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    audit.record(db, cur.user, "implant_patient_view", "implant_patient", p.id)
    db.commit()
    return _patient_out(db, p, full=True)


@router.patch("/patients/{patient_id}")
@session_only
def patch_patient(patient_id: str, body: PatientPatch, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    sent = body.model_dump(exclude_unset=True)
    by = cur.user.display_name or cur.user.email
    if body.retention_months is not None and (p.patient_user_id or p.invite_code_hash) \
            and body.retention_months not in im.PATIENT_KEEP:
        raise HTTPException(422, "With the patient's own account, keep the record for 1, 3 or 6 months")
    if body.guardian:
        p.consent = {**(p.consent or {}), "guardian": _guardian_row(body.guardian, by)}
        audit.record(db, cur.user, "implant_guardian_consent", "implant_patient", p.id)
    if body.date_of_birth is not None and body.date_of_birth != p.date_of_birth:
        minor = im.years(body.date_of_birth) < im.ADULT_AGE
        if minor and not (p.consent or {}).get("guardian"):
            raise HTTPException(422, "For a patient under 18, record a parent or guardian's name and signed consent")
        had = p.date_of_birth is not None
        p.date_of_birth = body.date_of_birth
        p.adult_confirmed = not minor
        dropped = 0
        if minor:
            # nothing photographic is kept for a child, and her own account goes too
            dropped = im.drop_pictures(db, p)
            im.unlink(p)
            p.consent = {**(p.consent or {}), "topless_ok": False}
        audit.record(db, cur.user, "implant_dob_set", "implant_patient", p.id, changed=had, minor=minor,
                     pictures_removed=dropped)
    if body.label is not None:
        p.label = body.label.strip()
    if body.retention_months is not None:
        p.retention_months = body.retention_months
    if body.notes is not None:
        p.notes = body.notes
    if body.measurements is not None:
        p.measurements = body.measurements.model_dump(exclude_none=True)
    if body.topless_ok is not None:
        if body.topless_ok and im.is_minor(p):
            raise HTTPException(409, im.MINOR_PHOTOS)
        p.consent = {**(p.consent or {}), "topless_ok": body.topless_ok}
    im.touch(p)
    audit.record(db, cur.user, "implant_patient_update", "implant_patient", p.id, fields=sorted(sent))
    db.commit()
    return _patient_out(db, p, full=True)


def _guardian_row(g: GuardianIn, by: str) -> dict:
    return {"name": g.name.strip(), "relation": g.relation.strip(), "consented_at": utcnow().isoformat(),
            "recorded_by": by}


@router.post("/patients/{patient_id}/invite")
@session_only
def invite_patient(patient_id: str, body: InviteIn | None = None, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_implant)):
    """A one-time code the patient types into My preview to see her own record. Shown once; only its hash is kept."""
    p = _patient(db, cur, patient_id)
    if p.date_of_birth is None:
        raise HTTPException(409, "Add the patient's date of birth first")
    if im.is_minor(p):
        raise HTTPException(409, "Patient accounts are for patients 18 and over")
    if p.patient_user_id:
        raise HTTPException(409, "The patient's account is already linked. Unlink it first to invite again.")
    keep = (body.retention_months if body else None) or p.retention_months
    if keep not in im.PATIENT_KEEP:
        raise HTTPException(409, "Choose how long to keep this record first: 1, 3 or 6 months")
    p.retention_months = keep
    code = im.new_invite(p)
    im.touch(p)
    audit.record(db, cur.user, "implant_patient_invite", "implant_patient", p.id, retention_months=keep)
    db.commit()
    return {"code": code, "expires_at": p.invite_expires_at, "patient": _patient_out(db, p, full=True)}


@router.delete("/patients/{patient_id}/patient")
@session_only
def unlink_patient(patient_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    was = p.patient_user_id
    im.unlink(p)
    im.touch(p)
    audit.record(db, cur.user, "implant_patient_unlink", "implant_patient", p.id, patient_user=was)
    db.commit()
    return _patient_out(db, p, full=True)


@router.post("/patients/{patient_id}/photos/{gen_id}/confirm-age")
@session_only
def confirm_photo_age(patient_id: str, gen_id: str, body: ConfirmAgeIn, db: Session = Depends(get_db),
                      cur: CurrentUser = Depends(require_implant)):
    """The age check flagged this photo: the surgeon types the date of birth again before it can be used."""
    p = _patient(db, cur, patient_id)
    g = db.get(Generation, gen_id)
    if g is None or g.target_type != "implant_photo" or g.target_id != p.id:
        raise HTTPException(404, "Photo not found")
    if not (g.params or {}).get("age_flag"):
        return _gen_out(g)
    if p.date_of_birth is None:
        raise HTTPException(409, "Add the patient's date of birth first")
    if body.date_of_birth != p.date_of_birth:
        audit.record(db, cur.user, "implant_age_reconfirm_mismatch", "implant_patient", p.id, photo=g.id)
        db.commit()
        raise HTTPException(422, "That date of birth doesn't match the patient's record")
    if im.is_minor(p):
        raise HTTPException(409, im.MINOR_PHOTOS)
    g.params = {**{k: v for k, v in (g.params or {}).items() if k != "age_flag"},
                "age_confirmed": {"by": cur.user.id, "at": utcnow().isoformat()}}
    im.touch(p)
    audit.record(db, cur.user, "implant_age_reconfirmed", "implant_patient", p.id, photo=g.id)
    db.commit()
    return _gen_out(g)


@router.delete("/patients/{patient_id}", status_code=204)
@session_only
def delete_patient(patient_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    audit.record(db, cur.user, "implant_patient_delete", "implant_patient", p.id)
    im.purge(db, p)
    db.commit()
    return Response(status_code=204)


@router.post("/patients/{patient_id}/end", status_code=204)
@session_only
def end_consultation(patient_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    """Session only: the consultation is over, so the patient and every photo and picture go now."""
    p = _patient(db, cur, patient_id)
    if p.retention_months:
        raise HTTPException(409, f"This patient is kept for {p.retention_months} months; delete them instead")
    audit.record(db, cur.user, "implant_session_end", "implant_patient", p.id)
    im.purge(db, p)
    db.commit()
    return Response(status_code=204)


@router.post("/patients/{patient_id}/consent-form")
@session_only
async def upload_consent_form(patient_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                              cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    data = await file.read(im.MAX_PHOTO_BYTES + 1)
    if len(data) > im.MAX_PHOTO_BYTES:
        raise HTTPException(413, "The file is over 25 MB")
    ext = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}.get(file.content_type or "")
    if ext is None:
        raise HTTPException(422, "Upload the signed form as a PDF, JPEG or PNG")
    rel = im.patient_rel(p) / f"consent{ext}"
    dest = get_settings().data_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    p.consent = {**(p.consent or {}), "form_path": rel.as_posix()}
    audit.record(db, cur.user, "implant_consent_form", "implant_patient", p.id)
    db.commit()
    return _patient_out(db, p, full=True)


@router.post("/patients/{patient_id}/photos", status_code=201)
@session_only
async def upload_photo(patient_id: str, file: UploadFile = File(...), view: str = Form(...),
                       garment: str = Form("garment"), crop: str = Form(""), db: Session = Depends(get_db),
                       cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    data = await file.read(im.MAX_PHOTO_BYTES + 1)
    box = None
    if crop.strip():
        try:
            box = tuple(float(v) for v in crop.split(","))
            assert len(box) == 4
        except (ValueError, AssertionError):
            raise HTTPException(422, "crop is x,y,w,h as fractions of the photo") from None
    g = im.save_photo(db, p, view, garment, data, box)
    audit.record(db, cur.user, "implant_photo_upload", "implant_patient", p.id, view=view, garment=garment,
                 age_flag=bool(g.params.get("age_flag")))
    db.commit()
    return _gen_out(g)


@router.post("/patients/{patient_id}/sessions", status_code=202)
@session_only
def create_session(patient_id: str, body: SessionIn, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    s = im.generate(db, p, [o.model_dump() for o in body.options], tuple(body.engines))
    audit.record(db, cur.user, "implant_generate", "implant_patient", p.id, session=s.id, options=len(body.options),
                 engines=body.engines)
    db.commit()
    return _patient_out(db, p, full=True)


@router.post("/patients/{patient_id}/check")
@session_only
def check_options(patient_id: str, body: SessionIn, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_implant)):
    """Implant size, expected cup change and the planner's warnings for each option, before anything renders."""
    p = _patient(db, cur, patient_id)
    return [planning.assess(o.model_dump(), p.measurements or {}) for o in body.options]


@router.patch("/photos/{gen_id}")
@session_only
def set_chest_box(gen_id: str, body: ChestBoxIn, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_implant)):
    """The surgeon's own chest area for a photo; renders after this keep everything outside it unchanged."""
    g = db.get(Generation, gen_id)
    if g is None or g.target_type != "implant_photo":
        raise HTTPException(404, "Photo not found")
    p = _patient(db, cur, g.target_id)
    box = clean_box(body.chest_box)
    if box is None:
        raise HTTPException(422, "The chest area is too small")
    g.params = {**(g.params or {}), "chest_box": box, "chest_box_from": "surgeon"}
    im.touch(p)
    audit.record(db, cur.user, "implant_chest_box", "implant_patient", p.id, view=g.params.get("view"))
    db.commit()
    return _gen_out(g)


@router.post("/patients/{patient_id}/sessions/{session_id}/approve")
@session_only
def approve_option(patient_id: str, session_id: str, body: ApproveIn, db: Session = Depends(get_db),
                   cur: CurrentUser = Depends(require_implant)):
    """The option the patient chose: its chart goes into the patient file (an earlier one stays as a version)."""
    p = _patient(db, cur, patient_id)
    s = _session(db, p, session_id)
    if body.option >= len(s.options):
        raise HTTPException(422, "No such option")
    picked = {g.params["implant"]["view"]: g for g in im.results(db, s)
              if g.params["implant"]["option"] == body.option and im.engine_of(g) == body.engine
              and g.status in ("ready", "approved")}
    if not picked:
        raise HTTPException(409, "That option has no finished pictures yet")
    chart = dict(p.chart or {})
    versions = list(chart.get("versions") or [])
    data = sheets.chart(p, cur.user.display_name or cur.user.email, im.photos(db, p), s.options[body.option],
                        picked, body.notes, body.engine)
    path = sheets.save_chart(p, data, len(versions) + 1)
    entry = {"session_id": s.id, "option": body.option, "engine": body.engine, "chart_path": path,
             "approved_at": utcnow().isoformat()}
    p.chart = {**entry, "versions": versions + [entry]}
    if body.notes:
        p.notes = body.notes
    im.touch(p)
    audit.record(db, cur.user, "implant_approve", "implant_patient", p.id, session=s.id, option=body.option,
                 engine=body.engine)
    db.commit()
    return _patient_out(db, p, full=True)


@router.get("/patients/{patient_id}/chart.pdf")
@session_only
def chart_pdf(patient_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    path = (p.chart or {}).get("chart_path")
    if not path or not (get_settings().data_dir / path).is_file():
        raise HTTPException(404, "No chart yet: approve an option first")
    audit.record(db, cur.user, "implant_chart_download", "implant_patient", p.id)
    db.commit()
    release(db)
    return FileResponse(get_settings().data_dir / path, media_type="application/pdf",
                        filename=f"chart {p.label}.pdf", headers=FILE_HEADERS)


@router.get("/patients/{patient_id}/sessions/{session_id}/consultation.pdf")
@session_only
def consultation_pdf(patient_id: str, session_id: str, db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_implant)):
    p = _patient(db, cur, patient_id)
    s = _session(db, p, session_id)
    data = sheets.consultation(p, cur.user.display_name or cur.user.email, im.photos(db, p), s.options,
                               im.results(db, s))
    audit.record(db, cur.user, "implant_pdf_download", "implant_patient", p.id, session=s.id)
    db.commit()
    return Response(data, media_type="application/pdf",
                    headers={**FILE_HEADERS, "Content-Disposition": f'attachment; filename="consultation {p.label}.pdf"'})


@router.get("/files/{gen_id}")
@session_only
def get_file(gen_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_implant)):
    g = db.get(Generation, gen_id)
    if g is None or g.target_type not in ("implant_photo", "implant_result") or not g.file_path:
        raise HTTPException(404, "Not found")
    pid = g.target_id if g.target_type == "implant_photo" else getattr(db.get(ImplantSession, g.target_id),
                                                                       "patient_id", None)
    _patient(db, cur, pid or "")
    root = (get_settings().data_dir / "clinic").resolve()
    path = (get_settings().data_dir / g.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Not found")
    release(db)
    return FileResponse(path, media_type=g.media_type or "image/png", headers=FILE_HEADERS)
