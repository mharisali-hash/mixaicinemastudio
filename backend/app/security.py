import hashlib
import hmac
from dataclasses import dataclass, replace
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ACTING_USER, VIA_API, ApiToken, Job, Membership, User, utcnow
from app.quota import QUOTA_CHANNEL, QUOTA_USER
from app import content_policy  # noqa: F401  (registers the check on new pictures and videos)

COOKIE_NAME = "mixai_session"
SESSION_MAX_AGE = 14 * 24 * 3600

# API tokens (contract v27): mxa_<8 hex prefix>_<secret>
TOKEN_PREFIX = "mxa_"
SCOPES = ("read", "generate", "manage")
METHOD_SCOPE = {"GET": "read", "HEAD": "read", "OPTIONS": "read", "POST": "generate", "PUT": "generate",
                "PATCH": "generate", "DELETE": "manage"}
SESSION_ONLY = "session"
TOUCH_EVERY = timedelta(minutes=1)
# Session.info key: the request's user hasn't confirmed their email, so it can't queue work (contract v38)
UNVERIFIED = "unverified"
UNVERIFIED_MSG = "Confirm your email to start creating"
# Session.info key: the request's user is on hold (contract v42), so it can't queue work either
BLOCKED = "blocked"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# what an account on hold may still POST: signing out, and saying it has read the notice
BLOCKED_ALLOWED = ("/api/auth/logout", "/api/account/notices")

_hasher = PasswordHasher()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().app_secret, salt="mixai-session")


def make_session_token(user_id: str, session_version: int = 0) -> str:
    return _serializer().dumps({"uid": user_id, "sv": session_version})


def read_session(token: str) -> tuple[str, int] | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or not data.get("uid"):
        return None
    # cookies from before contract v38 carry no "sv"; they match a user who never bumped it
    sv = data.get("sv", 0)
    return str(data["uid"]), sv if isinstance(sv, int) else -1


def read_session_token(token: str) -> str | None:
    found = read_session(token)
    return found[0] if found else None


@dataclass
class CurrentUser:
    user: User
    workspace_id: str
    role: str
    # set when the request came in with an API token; a browser session has no scope limits
    token_id: str | None = None
    scopes: frozenset[str] | None = None
    # app (cookie session), api (token on the REST routes) or mcp (/api/mcp); see app.visibility
    channel: str = "app"
    # ?via= on an app request; only an admin's counts (app.visibility.wanted)
    via: str | None = None

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def via_token(self) -> bool:
        return self.token_id is not None

    def can(self, scope: str) -> bool:
        return self.scopes is None or scope in self.scopes


def load_current(db: Session, user_id: str, workspace_id: str | None = None) -> CurrentUser | None:
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    # single workspace per user for now; workspace switching comes with multi-user
    q = select(Membership).where(Membership.user_id == user.id)
    if workspace_id:
        q = q.where(Membership.workspace_id == workspace_id)
    m = db.scalars(q.order_by(Membership.created_at)).first()
    if not m:
        return None
    return CurrentUser(user=user, workspace_id=m.workspace_id, role=m.role)


# ---------------------------------------------------------------- API tokens

def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def split_token(raw: str) -> str | None:
    """The lookup prefix of a well-formed token, else None."""
    if not raw.startswith(TOKEN_PREFIX):
        return None
    prefix, sep, secret = raw[len(TOKEN_PREFIX):].partition("_")
    if len(prefix) != 8 or not sep or len(secret) < 20:
        return None
    return prefix


def resolve_token(db: Session, raw: str) -> CurrentUser | None:
    """A presented API token -> the user it acts as, with its scopes. None for anything not live.

    Plain function on purpose: the MCP server resolves its Bearer header with this too."""
    prefix = split_token(raw)
    if prefix is None:
        return None
    row = db.scalars(select(ApiToken).where(ApiToken.prefix == prefix)).first()
    if row is None or not hmac.compare_digest(row.token_hash, hash_token(raw)):
        return None
    now = utcnow()
    if row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now):
        return None
    cur = load_current(db, row.user_id, row.workspace_id)
    # minted while allowed doesn't mean allowed now: PUBLIC_API off again, or an admin demoted. A viewer's token
    # lives on read-only (check_scope / the MCP role rule), as before, so this is the gate and not the minting rule
    if cur is None or not (get_settings().public_api or is_admin(cur)):
        return None
    if row.last_used_at is None or now - row.last_used_at >= TOUCH_EVERY:
        # once a minute is plenty for "last used", and keeps a busy script from writing on every call
        db.execute(update(ApiToken).where(ApiToken.id == row.id).values(last_used_at=now))
        db.commit()
    cur = replace(cur, token_id=row.id, scopes=frozenset(row.scopes or ()), channel="api")
    bind_session(db, cur)
    return cur


def bearer_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def token_scope(scope: str):
    """Overrides the scope a route needs from a token, e.g. a POST that only reads. SESSION_ONLY refuses tokens."""
    def mark(fn):
        fn.token_scope = scope
        return fn
    return mark


session_only = token_scope(SESSION_ONLY)


def required_scope(request: Request) -> str:
    route = request.scope.get("route")
    override = getattr(getattr(route, "endpoint", None), "token_scope", None)
    return override or METHOD_SCOPE.get(request.method.upper(), "manage")


def check_scope(cur: CurrentUser, needed: str) -> None:
    if needed == SESSION_ONLY:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API tokens can't be used here; sign in to the studio instead")
    if not cur.can(needed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"This token doesn't have the '{needed}' scope")


def authenticate(db: Session, request: Request) -> CurrentUser:
    raw = bearer_token(request)
    if raw is not None:
        # a Bearer header wins over any cookie; it's also what makes token calls CSRF-safe
        cur = resolve_token(db, raw)
        if cur is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, expired or revoked API token",
                                headers={"WWW-Authenticate": "Bearer"})
        check_scope(cur, required_scope(request))
        # a token stops working while its owner is on hold; it isn't revoked, so it works again after
        check_not_blocked(cur.user)
        # whatever this request creates is stamped via_api (models._stamp_via_api)
        db.info[VIA_API] = True
        return cur
    cur = session_user(db, request)
    if not cur:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    cur.via = request.query_params.get("via")
    if (cur.user.blocked_at is not None and request.method.upper() not in SAFE_METHODS
            and not request.url.path.startswith(BLOCKED_ALLOWED)):
        check_not_blocked(cur.user)
    bind_session(db, cur)
    return cur


def blocked_message(user: User) -> str:
    reason = (user.blocked_reason or "").strip()
    return f"Your account is on hold: {reason}" if reason else "Your account is on hold"


def check_not_blocked(user: User) -> None:
    if user.blocked_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, blocked_message(user))


def session_user(db: Session, request: Request) -> CurrentUser | None:
    token = request.cookies.get(COOKIE_NAME)
    found = read_session(token) if token else None
    if not found:
        return None
    cur = load_current(db, found[0])
    # a password change, reset or "sign out everywhere" bumps session_version and strands older cookies
    if cur is None or (cur.user.session_version or 0) != found[1]:
        return None
    return cur


def is_verified(user: User) -> bool:
    return user.email_verified_at is not None or not get_settings().email_verify


def bind_session(db: Session, cur: CurrentUser) -> None:
    """Jobs this session queues carry the user (models._stamp_user); an unverified user can't queue any, and
    everyone else's new jobs go through their package's limits (app.quota)."""
    db.info[ACTING_USER] = cur.user.id
    db.info[QUOTA_USER] = cur.user.id
    db.info[QUOTA_CHANNEL] = "api" if cur.token_id else "app"
    if is_verified(cur.user):
        db.info.pop(UNVERIFIED, None)
    else:
        db.info[UNVERIFIED] = True
    if cur.user.blocked_at is not None:
        db.info[BLOCKED] = blocked_message(cur.user)
    else:
        db.info.pop(BLOCKED, None)


@event.listens_for(Session, "before_flush")
def _gate_unverified(session, _ctx, _instances):
    # every route, token call and MCP tool that starts work adds a Job, so one check here covers them all
    if not any(isinstance(o, Job) for o in session.new):
        return
    if session.info.get(BLOCKED):
        raise HTTPException(status.HTTP_403_FORBIDDEN, session.info[BLOCKED])
    if session.info.get(UNVERIFIED):
        raise HTTPException(status.HTTP_403_FORBIDDEN, UNVERIFIED_MSG)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    return authenticate(db, request)


def require_editor(cur: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if cur.role == "viewer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Viewers can't change anything")
    return cur


# ---------------------------------------------------------------- who sees the API (PUBLIC_API, Update 4)

def is_admin(cur: CurrentUser) -> bool:
    return bool(cur.user.is_admin)


def require_admin(cur: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    # 404 rather than 403: admin routes aren't advertised to anyone else
    if not is_admin(cur):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return cur


# ---------------------------------------------------------------- staff and rights (contract v42)

def staff_of(user: User) -> str | None:
    if user.is_admin:
        return "admin"
    return "manager" if user.staff_role == "manager" else None


def is_moderator(cur: CurrentUser) -> bool:
    return staff_of(cur.user) is not None


def require_moderator(cur: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    # same as require_admin: anyone else gets a 404
    if not is_moderator(cur):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return cur


def require_feature(key: str, *, hide: bool = False):
    """Router guard for a studio. By default only changes are refused, so someone whose plan lost a studio can
    still open and download what they made there. hide=True refuses everything with a 404, for studios that
    aren't advertised at all (Print)."""
    def guard(request: Request, cur: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> CurrentUser:
        if not hide and request.method.upper() in SAFE_METHODS:
            return cur
        from app import rights

        r = rights.effective(db, cur.user)
        if r.allows(key):
            return cur
        if hide:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        raise HTTPException(status.HTTP_403_FORBIDDEN, r.refusal(key))
    return guard


def can_use_api(cur: CurrentUser) -> bool:
    if get_settings().public_api:
        return cur.role in ("owner", "editor")
    return is_admin(cur)


def can_read_docs(cur: CurrentUser) -> bool:
    s = get_settings()
    if not s.api_docs:
        return False
    return True if s.public_api else is_admin(cur)


def _hidden(request: Request, db: Session) -> CurrentUser:
    # signed out and not allowed look the same (404), so the feature isn't advertised
    try:
        return authenticate(db, request)
    except HTTPException as e:
        if e.status_code == status.HTTP_401_UNAUTHORIZED:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from None
        raise


def require_api_access(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    cur = _hidden(request, db)
    # a plan without the API still lists its tokens; using one is refused (quota.check_job)
    if not can_use_api(cur):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return cur


def require_docs_access(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    cur = _hidden(request, db)
    if not can_read_docs(cur):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return cur
