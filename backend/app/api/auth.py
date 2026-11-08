from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import accounts, mailer
from app.config import get_settings
from app.db import get_db
from app.implant.guard import has_access as has_implant_access
from app.models import User, utcnow
from app.ratelimit import SlidingWindow
from app.schemas import BlockedOut, EmailIn, LoginIn, PasswordChangeIn, ProfilePatch, ResetIn, SignupIn, UserOut
from app.security import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    CurrentUser,
    can_read_docs,
    can_use_api,
    get_current_user,
    hash_password,
    is_admin,
    is_verified,
    load_current,
    make_session_token,
    session_only,
    session_user,
    staff_of,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# failed logins per client IP. Behind Caddy, uvicorn's --proxy-headers makes request.client the real caller
LOGIN_FAILURES = SlidingWindow(limit=5, window_s=60)
# and per account, so a spread of IPs can't keep guessing one password
EMAIL_FAILURES = SlidingWindow(limit=10, window_s=15 * 60)
SIGNUP_IP = SlidingWindow(limit=5, window_s=3600)
SIGNUP_EMAIL = SlidingWindow(limit=3, window_s=24 * 3600)
# forgot-password and resend-verification share these: both send an email to whoever is named
MAIL_IP = SlidingWindow(limit=10, window_s=3600)
MAIL_EMAIL = SlidingWindow(limit=3, window_s=3600)
LIMITS = (LOGIN_FAILURES, EMAIL_FAILURES, SIGNUP_IP, SIGNUP_EMAIL, MAIL_IP, MAIL_EMAIL)

CHECK_INBOX = "If that address can be used, an email is on its way. Check your inbox."
BAD_LINK = "This link has expired or has already been used. Ask for a new one."


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limit(window: SlidingWindow, key: str, what: str = "attempts") -> None:
    wait = window.retry_after(key)
    if wait:
        raise HTTPException(429, f"Too many {what}. Try again in {wait} seconds.", headers={"Retry-After": str(wait)})


def _user_out(cur: CurrentUser) -> UserOut:
    u = cur.user
    return UserOut(id=u.id, email=u.email, display_name=u.display_name, workspace_id=cur.workspace_id, role=cur.role,
                   api_access=can_use_api(cur), api_docs=can_read_docs(cur), admin=is_admin(cur),
                   verified=is_verified(u), implant=has_implant_access(cur), staff=staff_of(u),
                   blocked=BlockedOut(reason=u.blocked_reason or "", at=u.blocked_at) if u.blocked_at else None)


def _set_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session_token(user.id, user.session_version or 0),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().is_prod,
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=get_settings().is_prod)


def _send_verify(db: Session, user: User) -> None:
    raw = accounts.issue_token(db, user, "verify")
    db.commit()
    mailer.deliver(mailer.verify_mail(user.email, user.display_name, mailer.link(f"/api/auth/verify?token={raw}")))


@router.get("/config")
def auth_config():
    s = get_settings()
    # google stays off until step 3 of M17 brings the sign-in flow
    # with no way to send mail, an admin confirms new accounts by hand from Admin > Users
    verify_by = "email" if mailer.can_send() else "admin"
    return {"signup": s.signup_mode, "google": False, "verify": s.email_verify, "verify_by": verify_by}


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _ip(request)
    email = accounts.normalise_email(body.email)
    _limit(LOGIN_FAILURES, ip, "sign-in attempts")
    _limit(EMAIL_FAILURES, email, "sign-in attempts")
    user = accounts.find_user(db, email)
    if not user or not verify_password(body.password, user.password_hash):
        LOGIN_FAILURES.hit(ip)
        EMAIL_FAILURES.hit(email)
        raise HTTPException(401, "Wrong email or password")
    if not user.is_active:
        # only someone holding the password gets this far, so saying why gives nothing away
        raise HTTPException(403, "This account has been switched off. Ask the studio's admin.")
    cur = load_current(db, user.id)
    if not cur:
        raise HTTPException(401, "This account has no workspace")
    user.last_login_at = utcnow()
    db.commit()
    _set_cookie(response, user)
    return _user_out(cur)


@router.post("/logout", status_code=204)
@session_only
def logout(_: CurrentUser = Depends(get_current_user)):
    resp = Response(status_code=204)
    _clear_cookie(resp)
    return resp


@router.post("/logout-everywhere", status_code=204)
@session_only
def logout_everywhere(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    accounts.sign_out_everywhere(cur.user)
    db.commit()
    resp = Response(status_code=204)
    _clear_cookie(resp)
    return resp


@router.post("/signup", status_code=202)
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    if s.signup_mode != "open":
        raise HTTPException(403, "New accounts are by invitation only" if s.signup_mode == "invite"
                            else "Sign-up is closed")
    email = accounts.normalise_email(body.email)
    ip = _ip(request)
    _limit(SIGNUP_IP, ip, "sign-ups from here")
    _limit(SIGNUP_EMAIL, email, "sign-ups for this address")
    if not accounts.looks_like_email(email):
        raise HTTPException(422, "That doesn't look like an email address")
    try:
        accounts.check_password(body.password)
    except accounts.AccountError as e:
        raise HTTPException(422, str(e)) from None
    SIGNUP_IP.hit(ip)
    SIGNUP_EMAIL.hit(email)
    existing = accounts.find_user(db, email)
    if existing is not None:
        # the same answer as a new account, and about the same time (an argon2 hash either way)
        hash_password(body.password)
        if existing.is_active:
            mailer.deliver(mailer.exists_mail(email, mailer.link("/login"), mailer.link("/forgot-password")))
        return {"detail": CHECK_INBOX}
    try:
        user = accounts.create_account(db, email, body.name, body.password, verified=not s.email_verify)
    except accounts.AccountExists:
        # lost a race with another sign-up for the same address
        db.rollback()
        return {"detail": CHECK_INBOX}
    except accounts.AccountError as e:
        raise HTTPException(422, str(e)) from None
    db.commit()
    if s.email_verify and mailer.can_send():
        _send_verify(db, user)
    return {"detail": CHECK_INBOX}


@router.get("/verify")
def verify(token: str = "", db: Session = Depends(get_db)):
    row = accounts.peek_token(db, token, ("verify",))
    user = accounts.take_token(db, token, ("verify",))
    if user is None:
        # a mail scanner may have opened the link first; the address is confirmed either way
        done = row is not None and (u := db.get(User, row.user_id)) is not None and u.email_verified_at is not None
        return RedirectResponse("/login?verified=1" if done else "/verify-email?status=expired", status_code=303)
    user.last_login_at = utcnow()
    db.commit()
    resp = RedirectResponse("/?verified=1", status_code=303)
    _set_cookie(resp, user)
    return resp


@router.post("/verify/resend")
def resend_verification(body: EmailIn, request: Request, db: Session = Depends(get_db)):
    cur = session_user(db, request)
    user = cur.user if cur else (accounts.find_user(db, body.email) if body.email.strip() else None)
    if cur is None and not body.email.strip():
        raise HTTPException(422, "Enter your email address")
    key = user.email if user else accounts.normalise_email(body.email)
    _limit(MAIL_IP, _ip(request), "emails from here")
    _limit(MAIL_EMAIL, f"verify:{key}", "emails to this address")
    MAIL_IP.hit(_ip(request))
    MAIL_EMAIL.hit(f"verify:{key}")
    if user is not None and user.is_active and user.email_verified_at is None:
        _send_verify(db, user)
    return {"detail": CHECK_INBOX}


@router.post("/forgot")
def forgot(body: EmailIn, request: Request, db: Session = Depends(get_db)):
    email = accounts.normalise_email(body.email)
    ip = _ip(request)
    _limit(MAIL_IP, ip, "emails from here")
    MAIL_IP.hit(ip)
    # over the per-address limit it quietly sends nothing: a 429 here would say the address is known
    if email and not MAIL_EMAIL.retry_after(f"reset:{email}"):
        MAIL_EMAIL.hit(f"reset:{email}")
        user = accounts.find_user(db, email)
        if user is not None and user.is_active:
            raw = accounts.issue_token(db, user, "reset")
            db.commit()
            mailer.deliver(mailer.reset_mail(user.email, mailer.link(f"/reset-password?token={quote(raw)}")))
    return {"detail": "If there's an account for that address, a reset link is on its way. Check your inbox."}


@router.post("/reset", response_model=UserOut)
def reset(body: ResetIn, response: Response, db: Session = Depends(get_db)):
    try:
        accounts.check_password(body.password)
    except accounts.AccountError as e:
        raise HTTPException(422, str(e)) from None
    user = accounts.take_token(db, body.token, ("reset", "invite"))
    cur = load_current(db, user.id) if user else None
    if user is None or cur is None:
        raise HTTPException(400, BAD_LINK)
    accounts.set_password(user, body.password)
    user.last_login_at = utcnow()
    db.commit()
    _set_cookie(response, user)
    return _user_out(cur)


@router.post("/password", response_model=UserOut)
@session_only
def change_password(body: PasswordChangeIn, response: Response, db: Session = Depends(get_db),
                    cur: CurrentUser = Depends(get_current_user)):
    # 400, not 401: the app treats a 401 as "signed out"
    if not verify_password(body.current_password, cur.user.password_hash):
        raise HTTPException(400, "Your current password isn't right")
    try:
        accounts.set_password(cur.user, body.new_password)
    except accounts.AccountError as e:
        raise HTTPException(422, str(e)) from None
    db.commit()
    # every other browser is signed out; this one gets a cookie for the new version
    _set_cookie(response, cur.user)
    return _user_out(cur)


@router.get("/dev-login")
def dev_login(request: Request):
    # Local convenience only. Anything proxied (Caddy adds X-Forwarded-For) or not
    # explicitly in dev mode gets nothing, so a server deployment never hands this out.
    s = get_settings()
    local = request.client is not None and request.client.host in ("127.0.0.1", "::1", "localhost")
    if s.app_env.lower() != "dev" or not s.dev_login_prefill or not local or "x-forwarded-for" in request.headers:
        return {"enabled": False}
    return {"enabled": True, "email": s.admin_email, "password": s.admin_password}


@router.get("/me", response_model=UserOut)
@session_only
def me(cur: CurrentUser = Depends(get_current_user)):
    return _user_out(cur)


@router.patch("/me", response_model=UserOut)
@session_only
def update_me(body: ProfilePatch, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    name = body.display_name.strip()
    if not name:
        raise HTTPException(422, "Enter a name")
    cur.user.display_name = name
    db.commit()
    return _user_out(cur)
