"""Who may use which studio and which model (contract v42).

One answer, worked out in this order (the first layer that has an opinion wins):
  1. admins can use everything;
  2. the user's own override (user.rights), set by an admin, either way;
  3. a studio or model nobody has released yet is off (deny by default for new things);
  4. the package: package.features switches studios off, package.models is an allow-list.
Quota (app.quota) asks this on every new job, the routes and MCP ask it before that, and /account/usage hands
the result to the app so it can hide what isn't there. Licence gating (models_catalog.hidden) sits underneath
all of this and is never shown to anyone.
"""
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models_catalog as mc
from app import packages as pk
from app.models import ACTING_USER, RightRelease, User, utcnow

FEATURE, MODEL = "feature", "model"
KINDS = (FEATURE, MODEL)
# what every install had before rights existed; ensure_baseline releases these so nobody loses anything.
# print, presenter, bulk_edit and implant are left out on purpose: admin-only (implant: per-account) until opened
BASELINE_FEATURES = ("image", "video", "audio", "character", "ads", "poetry", "podcast", "dub", "stories", "3d",
                     "api", "song_voices")
# features that follow their own switch rather than packages
OWN_SWITCH = {"implant"}
NOT_IN_PLAN = "This studio isn't part of your plan"


@dataclass
class Rights:
    admin: bool
    features: dict[str, bool] = field(default_factory=dict)
    feature_why: dict[str, str] = field(default_factory=dict)
    model_overrides: dict[str, bool] = field(default_factory=dict)
    released_models: frozenset[str] = frozenset()
    package_models: frozenset[str] | None = None
    package_name: str = ""
    blocked: bool = False

    def allows(self, key: str) -> bool:
        return self.admin or self.features.get(key, False)

    def model_ok(self, model_id: str) -> bool:
        if self.admin:
            return True
        if model_id in self.model_overrides:
            return self.model_overrides[model_id]
        if model_id not in self.released_models:
            return False
        return self.package_models is None or model_id in self.package_models

    def models(self) -> list[str] | None:
        """Every catalogue id this user may pick; None for an admin (all of them)."""
        return None if self.admin else [mid for mid in mc.MODELS if self.model_ok(mid)]

    def refusal(self, key: str) -> str:
        return self.feature_why.get(key) or NOT_IN_PLAN


def released(db: Session) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {FEATURE: set(), MODEL: set()}
    for kind, key in db.execute(select(RightRelease.kind, RightRelease.key)):
        out.setdefault(kind, set()).add(key)
    return out


def overrides(user: User) -> tuple[dict[str, bool], dict[str, bool]]:
    raw = user.rights or {}
    feats = {k: bool(v) for k, v in (raw.get("features") or {}).items() if k in pk.FEATURES}
    models = {k: bool(v) for k, v in (raw.get("models") or {}).items()}
    return feats, models


def effective(db: Session, user: User, lim=None) -> Rights:
    if user.is_admin:
        return Rights(admin=True, features={k: True for k in pk.FEATURES}, blocked=user.blocked_at is not None)
    from app import quota

    lim = lim or quota.limits_for(db, user)
    rel = released(db)
    feat_over, model_over = overrides(user)
    name = lim.package.name if lim.package else "Your"
    feats: dict[str, bool] = {}
    why: dict[str, str] = {}
    for key, label in pk.FEATURES.items():
        if key == "implant":
            on = bool(user.implant_access)
        elif key in feat_over:
            on = feat_over[key]
            if not on:
                why[key] = f"{label} is switched off for your account"
        elif key not in rel[FEATURE]:
            on = False
        else:
            on = lim.features.get(key, True) is not False
            if not on:
                why[key] = f"The {name} package doesn't include {label}"
        feats[key] = on
    return Rights(admin=False, features=feats, feature_why=why, model_overrides=model_over,
                  released_models=frozenset(rel[MODEL]),
                  package_models=frozenset(lim.models) if lim.models is not None else None,
                  package_name=name, blocked=user.blocked_at is not None)


def for_session(db: Session) -> Rights | None:
    """The rights of whoever this session acts for: the signed-in user on a request, the job's owner in the
    worker. None when nobody is (start-up, scripts, tests poking the DB): those aren't limited."""
    uid = db.info.get(ACTING_USER)
    user = db.get(User, uid) if uid else None
    return effective(db, user) if user is not None else None


def check_model(r: Rights | None, model_id: str) -> None:
    if r is None or r.model_ok(model_id):
        return
    if r.package_models is not None and model_id not in r.package_models and model_id not in r.model_overrides:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"The {r.package_name} package doesn't include the model "
                                                       f"'{model_id}'. Choose another one.")
    raise HTTPException(status.HTTP_403_FORBIDDEN, f"The model '{model_id}' isn't part of your plan. "
                                                   "Choose another one.")


def choose(db: Session, model_id: str | None, type_: str, prompt: str = "", **kw):
    """mc.choose for the person this session acts for: Auto only picks models they may use, and asking for one
    they may not is a 403 here rather than after the job is queued."""
    r = for_session(db)
    allowed = None if r is None or r.admin else r.model_ok
    m, params = mc.choose(model_id, type_, prompt, allowed=allowed, **kw)
    check_model(r, m.id)
    return m, params


def get(db: Session, model_id: str | None, type_: str):
    m = mc.get(model_id, type_)
    check_model(for_session(db), m.id)
    return m


# ---------------------------------------------------------------- release

def ensure_baseline(db: Session) -> None:
    """On the first start with this table empty: release what this install already offered."""
    if db.scalars(select(RightRelease.id).limit(1)).first():
        return
    db.add_all(RightRelease(kind=FEATURE, key=k) for k in BASELINE_FEATURES)
    db.add_all(RightRelease(kind=MODEL, key=mid) for mid in mc.MODELS)
    db.flush()


def known(kind: str, key: str) -> bool:
    return key in (pk.FEATURES if kind == FEATURE else mc.MODELS) and key not in OWN_SWITCH


def release(db: Session, kind: str, key: str, actor: User) -> bool:
    """True when it changed. Released means packages decide; until then only admins (and overrides) have it."""
    if db.scalars(select(RightRelease).where(RightRelease.kind == kind, RightRelease.key == key)).first():
        return False
    db.add(RightRelease(kind=kind, key=key, released_by=actor.id, released_at=utcnow()))
    db.flush()
    return True


def withdraw(db: Session, kind: str, key: str) -> bool:
    row = db.scalars(select(RightRelease).where(RightRelease.kind == kind, RightRelease.key == key)).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def clean_overrides(raw: dict | None) -> dict:
    """An admin's PATCH: unknown keys are a 422 rather than silently kept, null drops an override."""
    out: dict = {"features": {}, "models": {}}
    for part, valid in (("features", pk.FEATURES), ("models", mc.MODELS)):
        for k, v in ((raw or {}).get(part) or {}).items():
            if k not in valid or k in OWN_SWITCH:
                raise HTTPException(422, f"Unknown {part[:-1]} '{k}'")
            if v is not None:
                out[part][k] = bool(v)
    return out


# ---------------------------------------------------------------- the worker

def check_render(ctx, gen) -> None:
    """Before a GPU is touched: a job whose owner went on hold after queueing it isn't made. Models are checked
    where they're chosen (choose/get above, and quota for every new job); the fixed helper steps inside a studio
    (title music, 3D back views) belong to the studio's own right, not the model list."""
    job = ctx.job
    if not job.user_id:
        return
    user = ctx.db.get(User, job.user_id)
    if user is not None and user.blocked_at is not None and not user.is_admin:
        raise RuntimeError("Your account is on hold, so this wasn't made")
