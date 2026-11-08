"""Accounts (M17 step 1, contract v38): sign-up, email links, sessions, the unverified gate, admin users, Origin
check and the token re-check."""
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import accounts, mailer, manage
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import ApiToken, EmailToken, Job, Membership, User, Workspace, utcnow
from app.security import make_session_token
from tests.conftest import ADMIN
from tests.test_api_tokens import _bearer, _mint, flags, login  # noqa: F401  (fixtures)
from tests.test_mcp import Mcp

PW = "a-long-password-1"


@pytest.fixture
def mails(monkeypatch):
    sent: list[mailer.Mail] = []
    monkeypatch.setattr(mailer, "deliver", sent.append)
    monkeypatch.setattr(mailer, "can_send", lambda: True)
    return sent


def _link(mail: mailer.Mail) -> str:
    return re.search(r"https?://\S+token=\S+", mail.text).group(0)


def _token(mail: mailer.Mail) -> str:
    return parse_qs(urlsplit(_link(mail)).query)["token"][0]


def _path(mail: mailer.Mail) -> str:
    parts = urlsplit(_link(mail))
    return f"{parts.path}?{parts.query}"


def signup(c, email="nia@example.com", password=PW, name="Nia"):
    return c.post("/api/auth/signup", json={"email": email, "password": password, "name": name})


def make_user(email="ben@example.com", name="Ben", verified=True, admin=False) -> str:
    with SessionLocal() as db:
        u = accounts.create_account(db, email, name, PW, verified=verified, admin=admin)
        db.commit()
        return u.id


# ---------------------------------------------------------------- sign-up and verification

def test_config_reports_the_modes(anon, flags, mails):  # noqa: F811
    assert anon.get("/api/auth/config").json() == {"signup": "open", "google": False, "verify": True, "verify_by": "email"}
    flags(signup="Invite", email_verify=False)
    assert anon.get("/api/auth/config").json() == {"signup": "invite", "google": False, "verify": False,
                                                   "verify_by": "email"}
    flags(signup="nonsense")
    assert anon.get("/api/auth/config").json()["signup"] == "closed"


def test_no_mail_means_an_admin_confirms(anon, client, monkeypatch):
    sent: list = []
    monkeypatch.setattr(mailer, "deliver", sent.append)
    assert anon.get("/api/auth/config").json()["verify_by"] == "admin"
    assert signup(anon).status_code == 202 and sent == []
    uid = next(u["id"] for u in client.get("/api/admin/users?status=unverified").json() if u["email"] == "nia@example.com")
    assert client.patch(f"/api/admin/users/{uid}", json={"verified": True}).json()["verified"] is True
    with SessionLocal() as db:
        assert db.get(User, uid).email_verified_at is not None


def test_signup_makes_a_private_studio_and_sends_a_link(anon, mails):
    r = signup(anon)
    assert r.status_code == 202 and "Check your inbox" in r.json()["detail"]
    # no cookie: signing in straight away would tell a new address from a known one
    assert "set-cookie" not in r.headers
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.email == "nia@example.com")).one()
        assert u.email_verified_at is None and not u.is_admin and u.handle == "nia" and u.session_version == 0
        m = db.scalars(select(Membership).where(Membership.user_id == u.id)).one()
        ws = db.get(Workspace, m.workspace_id)
        assert m.role == "owner" and ws.name == "Nia's studio" and ws.owner_user_id == u.id
        tok = db.scalars(select(EmailToken).where(EmailToken.user_id == u.id)).one()
        assert tok.purpose == "verify" and tok.token_hash != _token(mails[0])
        assert timedelta(hours=47) < tok.expires_at - utcnow() <= timedelta(hours=48)
    [mail] = mails
    assert mail.to == "nia@example.com" and mail.subject == "Confirm your email"
    assert _link(mail).startswith(get_settings().public_base + "/api/auth/verify?token=")
    assert "Cormorant Garamond" in mail.html and "#f4ead5" in mail.html and _link(mail) in mail.text


def test_signup_rules(anon, mails, flags):  # noqa: F811
    assert signup(anon, password="short").status_code == 422
    assert signup(anon, email="not-an-email").status_code == 422
    assert mails == []
    flags(signup="invite")
    r = signup(anon)
    assert r.status_code == 403 and "invitation" in r.json()["detail"]
    flags(signup="closed")
    assert signup(anon).status_code == 403
    flags(signup="open", email_verify=False)
    assert signup(anon, email="quick@example.com").status_code == 202
    assert mails == []  # nothing to confirm
    assert anon.post("/api/auth/login", json={"email": "quick@example.com", "password": PW}).json()["verified"]


def test_signup_never_says_an_address_is_taken(anon, mails):
    new = signup(anon, email="fresh@example.com")
    known = signup(anon, email=ADMIN["email"].upper())
    assert (new.status_code, new.json()) == (known.status_code, known.json())
    assert [m.subject for m in mails] == ["Confirm your email", "You already have an account"]
    assert mails[1].to == ADMIN["email"]
    with SessionLocal() as db:
        assert db.scalars(select(User).where(User.email == ADMIN["email"])).one().display_name == "Tester"


def test_signup_is_rate_limited(anon, mails):
    for i in range(3):
        assert signup(anon, email="same@example.com", name=f"n{i}").status_code == 202
    assert signup(anon, email="same@example.com").status_code == 429
    for i in range(2):
        assert signup(anon, email=f"other{i}@example.com").status_code == 202
    r = signup(anon, email="sixth@example.com")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_verify_link_signs_in_and_works_once(anon, mails):
    signup(anon)
    path = _path(mails[0])
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?verified=1"
    me = anon.get("/api/auth/me").json()
    assert me["email"] == "nia@example.com" and me["verified"] and not me["admin"]
    # opened again (or first by a mail scanner): already confirmed, so it points at sign-in, no new session
    with TestClient(app) as other:
        r = other.get(path, follow_redirects=False)
        assert r.headers["location"] == "/login?verified=1" and "set-cookie" not in r.headers


def test_bad_and_expired_verify_links(anon, mails):
    assert anon.get("/api/auth/verify?token=nope", follow_redirects=False).headers["location"] == \
        "/verify-email?status=expired"
    signup(anon)
    with SessionLocal() as db:
        db.scalars(select(EmailToken)).one().expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    r = anon.get(_path(mails[0]), follow_redirects=False)
    assert r.headers["location"] == "/verify-email?status=expired"
    with SessionLocal() as db:
        assert db.scalars(select(User).where(User.email == "nia@example.com")).one().email_verified_at is None


def test_resend_verification(anon, mails):
    signup(anon)
    first = _path(mails[0])
    r = anon.post("/api/auth/verify/resend", json={"email": "NIA@example.com"})
    assert r.status_code == 200 and len(mails) == 2
    # only the newest link works
    assert anon.get(first, follow_redirects=False).headers["location"] == "/verify-email?status=expired"
    # unknown and already-confirmed addresses get the same answer and no email
    assert anon.post("/api/auth/verify/resend", json={"email": "who@example.com"}).json() == r.json()
    assert anon.post("/api/auth/verify/resend", json={"email": ADMIN["email"]}).json() == r.json()
    assert len(mails) == 2
    assert anon.post("/api/auth/verify/resend", json={}).status_code == 422
    # signed in and unverified: no address needed
    assert anon.post("/api/auth/login", json={"email": "nia@example.com", "password": PW}).status_code == 200
    assert anon.post("/api/auth/verify/resend", json={}).status_code == 200 and len(mails) == 3
    # three an hour per address, counting the one asked for by email above
    assert anon.post("/api/auth/verify/resend", json={}).status_code == 200
    assert anon.post("/api/auth/verify/resend", json={}).status_code == 429


# ---------------------------------------------------------------- forgot and reset

def test_forgot_and_reset(anon, client, mails):
    known = anon.post("/api/auth/forgot", json={"email": ADMIN["email"]})
    unknown = anon.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    [mail] = mails
    assert mail.subject == "Reset your password" and "/reset-password?token=" in _link(mail)
    with SessionLocal() as db:
        tok = db.scalars(select(EmailToken).where(EmailToken.purpose == "reset")).one()
        assert timedelta(minutes=59) < tok.expires_at - utcnow() <= timedelta(hours=1)

    token = _token(mail)
    assert anon.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 422
    r = anon.post("/api/auth/reset", json={"token": token, "password": "brand-new-pass-99"})
    assert r.status_code == 200 and r.json()["email"] == ADMIN["email"]
    assert anon.get("/api/auth/me").status_code == 200
    # the old browser session is gone, the old password too, and the link can't be used twice
    assert client.get("/api/auth/me").status_code == 401
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 401
        assert c.post("/api/auth/login", json={**ADMIN, "password": "brand-new-pass-99"}).status_code == 200
    again = anon.post("/api/auth/reset", json={"token": token, "password": "another-pass-123"})
    assert again.status_code == 400 and "expired" in again.json()["detail"]


def test_expired_reset_link_and_quiet_per_address_limit(anon, mails):
    anon.post("/api/auth/forgot", json={"email": ADMIN["email"]})
    with SessionLocal() as db:
        db.scalars(select(EmailToken)).one().expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert anon.post("/api/auth/reset", json={"token": _token(mails[0]), "password": "brand-new-pass-99"}) \
        .status_code == 400
    for _ in range(4):
        assert anon.post("/api/auth/forgot", json={"email": ADMIN["email"]}).status_code == 200
    # three an hour per address; the fourth answers the same but sends nothing
    assert len(mails) == 3


# ---------------------------------------------------------------- sessions

def test_old_cookie_without_version_still_works_until_bumped(anon, client):
    uid = client.get("/api/auth/me").json()["id"]
    from app.security import _serializer

    anon.cookies.set("mixai_session", _serializer().dumps({"uid": uid}))
    assert anon.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout-everywhere").status_code == 204
    assert anon.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.get(User, uid).session_version == 1
    anon.cookies.set("mixai_session", make_session_token(uid, 1))
    assert anon.get("/api/auth/me").status_code == 200


def test_change_password_keeps_this_browser_only(client, login):  # noqa: F811
    other = login(ADMIN)
    bad = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "brand-new-pass-99"})
    assert bad.status_code == 400
    assert client.post("/api/auth/password", json={"current_password": ADMIN["password"], "new_password": "short"}) \
        .status_code == 422
    ok = client.post("/api/auth/password", json={"current_password": ADMIN["password"],
                                                 "new_password": "brand-new-pass-99"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_login_records_time_and_refuses_switched_off_accounts(anon):
    uid = make_user()
    assert anon.post("/api/auth/login", json={"email": "ben@example.com", "password": PW}).status_code == 200
    with SessionLocal() as db:
        u = db.get(User, uid)
        assert u.last_login_at is not None
        accounts.deactivate(db, u)
        db.commit()
    assert anon.get("/api/auth/me").status_code == 401
    r = anon.post("/api/auth/login", json={"email": "ben@example.com", "password": PW})
    assert r.status_code == 403 and "switched off" in r.json()["detail"]


def test_login_is_limited_per_email_too():
    from app.api import auth

    for i in range(10):
        auth.EMAIL_FAILURES.hit(ADMIN["email"])
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json=ADMIN)
        assert r.status_code == 429


def test_profile_name_can_be_changed(client):
    r = client.patch("/api/auth/me", json={"display_name": "  Haris  "})
    assert r.status_code == 200 and r.json()["display_name"] == "Haris"
    assert client.patch("/api/auth/me", json={"display_name": "   "}).status_code == 422


# ---------------------------------------------------------------- the unverified gate

def test_unverified_user_can_look_but_not_queue_work(anon, login, flags, mails):  # noqa: F811
    flags(public_api=True)
    signup(anon)
    c = login({"email": "nia@example.com", "password": PW})
    assert c.get("/api/auth/me").json()["verified"] is False
    assert c.get("/api/projects").status_code == 200
    assert c.post("/api/projects", json={"title": "Mine", "authoring_mode": "scene_by_scene"}).status_code == 201
    r = c.post("/api/images/generate", json={"prompt": "a red kite"})
    assert r.status_code == 403 and r.json()["detail"] == "Confirm your email to start creating"

    # the same through a token, on REST and MCP
    token = _mint(c, ["read", "generate"])["token"]
    r = anon.post("/api/images/generate", json={"prompt": "a red kite"}, headers=_bearer(token))
    assert r.status_code == 403 and "Confirm your email" in r.json()["detail"]
    res = Mcp(anon, token).call("generate_image", prompt="a red kite")
    assert res["isError"] and "Confirm your email" in res["content"][0]["text"]
    with SessionLocal() as db:
        assert db.scalars(select(Job)).all() == []

    c.get(_path(mails[0]))
    assert c.post("/api/images/generate", json={"prompt": "a red kite"}).status_code == 202


def test_jobs_carry_the_user_and_children_inherit_it(client, fast_driver):
    uid = client.get("/api/auth/me").json()["id"]
    assert client.post("/api/images/generate", json={"prompt": "a red kite"}).status_code == 202
    from app.worker import process_one
    from app.models import UsageLedger

    while process_one(driver_factory=fast_driver):
        pass
    with SessionLocal() as db:
        jobs = db.scalars(select(Job)).all()
        assert jobs and {j.user_id for j in jobs} == {uid}
        assert {r.user_id for r in db.scalars(select(UsageLedger))} == {uid}


# ---------------------------------------------------------------- admin users

def test_admin_users_list_and_patch(client, login, mails):  # noqa: F811
    ben = make_user()
    make_user("cara@example.com", "Cara", verified=False)
    rows = client.get("/api/admin/users").json()
    assert {r["email"] for r in rows} == {ADMIN["email"], "ben@example.com", "cara@example.com"}
    me = next(r for r in rows if r["email"] == ADMIN["email"])
    assert me["is_admin"] and me["verified"] and me["gpu_min_today"] == 0 and me["jobs"] == 0
    assert [r["email"] for r in client.get("/api/admin/users?q=CAR").json()] == ["cara@example.com"]
    assert [r["email"] for r in client.get("/api/admin/users?status=unverified").json()] == ["cara@example.com"]
    assert [r["email"] for r in client.get("/api/admin/users?status=admin").json()] == [ADMIN["email"]]

    b = login({"email": "ben@example.com", "password": PW})
    promoted = client.patch(f"/api/admin/users/{ben}", json={"is_admin": True}).json()
    assert promoted["is_admin"] and b.get("/api/auth/me").json()["admin"]
    tok = _mint(b, ["read"])["token"]
    r = client.patch(f"/api/admin/users/{ben}", json={"is_active": False, "is_admin": False})
    assert r.status_code == 200 and not r.json()["is_active"]
    # switched off: signed out, tokens revoked, no way back in
    assert b.get("/api/auth/me").status_code == 401
    assert b.get("/api/projects", headers=_bearer(tok)).status_code == 401
    with SessionLocal() as db:
        assert db.scalars(select(ApiToken)).one().revoked_at is not None
    assert client.patch(f"/api/admin/users/{ben}", json={"is_active": True}).json()["is_active"]

    own = client.get("/api/auth/me").json()["id"]
    assert client.patch(f"/api/admin/users/{own}", json={"is_active": False}).status_code == 422
    assert client.patch(f"/api/admin/users/{own}", json={"is_admin": False}).status_code == 422
    assert client.patch("/api/admin/users/nope", json={"verified": True}).status_code == 404

    assert client.post(f"/api/admin/users/{ben}/send-reset").status_code == 202
    assert mails[-1].subject == "Reset your password" and mails[-1].to == "ben@example.com"


def test_admin_invites_a_user(client, anon, mails, flags):  # noqa: F811
    flags(signup="invite")
    r = client.post("/api/admin/users", json={"email": "Dee@Example.com", "name": "Dee"})
    assert r.status_code == 201 and r.json()["email"] == "dee@example.com" and not r.json()["verified"]
    assert client.post("/api/admin/users", json={"email": "dee@example.com"}).status_code == 409
    assert client.post("/api/admin/users", json={"email": "nope"}).status_code == 422
    [mail] = mails
    assert "invite=1" in _link(mail)
    # the invite link sets the password, confirms the address and signs in
    r = anon.post("/api/auth/reset", json={"token": _token(mail), "password": "dees-own-pass-1"})
    assert r.status_code == 200 and r.json()["verified"] and r.json()["role"] == "owner"
    assert r.json()["workspace_id"] != client.get("/api/auth/me").json()["workspace_id"]


def test_admin_routes_are_hidden_from_everyone_else(anon, login):  # noqa: F811
    uid = make_user()
    b = login({"email": "ben@example.com", "password": PW})
    assert b.get("/api/admin/users").status_code == 404
    assert b.patch(f"/api/admin/users/{uid}", json={"is_admin": True}).status_code == 404
    assert b.post("/api/admin/users", json={"email": "x@example.com"}).status_code == 404
    assert b.post("/api/admin/mark-api", json={}).status_code == 404
    assert b.get("/api/system/comfy-check").status_code == 404
    assert b.get("/api/system/llm-check?ping=false").status_code == 404
    assert anon.get("/api/admin/users").status_code == 401


def test_admin_is_the_column_not_the_email(client, login, flags):  # noqa: F811
    flags(admin_email="someone@else.example")
    assert client.get("/api/auth/me").json()["admin"] is True
    make_user()
    b = login({"email": "ben@example.com", "password": PW})
    flags(admin_email="ben@example.com")
    assert b.get("/api/auth/me").json()["admin"] is False


# ---------------------------------------------------------------- tokens re-check the gate

def test_tokens_die_when_the_api_closes_to_their_owner(anon, login, flags):  # noqa: F811
    flags(public_api=True)
    make_user()
    b = login({"email": "ben@example.com", "password": PW})
    tok = _mint(b, ["read"])["token"]
    assert anon.get("/api/projects", headers=_bearer(tok)).status_code == 200
    assert Mcp(anon, tok).raw("tools/list").status_code == 200
    flags(public_api=False)
    assert anon.get("/api/projects", headers=_bearer(tok)).status_code == 401
    assert Mcp(anon, tok).raw("tools/list").status_code == 401


# ---------------------------------------------------------------- Origin check

def test_cookie_writes_from_another_site_are_refused(client):
    body = {"title": "X", "authoring_mode": "scene_by_scene"}
    r = client.post("/api/projects", json=body, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "another site" in r.json()["detail"]
    assert client.post("/api/projects", json=body, headers={"Referer": "https://evil.example/page"}).status_code == 403
    assert client.post("/api/projects", json=body, headers={"Origin": "null"}).status_code == 403
    # the frontend's own origins, the app talking to itself, and no Origin at all (curl, tests) are fine
    assert client.post("/api/projects", json=body, headers={"Origin": "http://localhost:5173"}).status_code == 201
    assert client.post("/api/projects", json=body, headers={"Origin": "http://testserver"}).status_code == 201
    assert client.post("/api/projects", json=body).status_code == 201
    # reads aren't checked
    assert client.get("/api/projects", headers={"Origin": "https://evil.example"}).status_code == 200


def test_bearer_calls_skip_the_origin_check(client, anon):
    tok = _mint(client, ["read", "generate"])["token"]
    r = anon.post("/api/projects", json={"title": "X", "authoring_mode": "scene_by_scene"},
                  headers={**_bearer(tok), "Origin": "https://evil.example"})
    assert r.status_code == 201


# ---------------------------------------------------------------- manage.py

def test_manage_create_user(monkeypatch, anon, capsys):
    monkeypatch.setenv("MIXAI_NEW_PASSWORD", "cli-made-pass-1")
    assert manage.main(["create-user", "--email", "Eve@Example.com", "--name", "Eve", "--admin"]) == 0
    assert manage.main(["create-user", "--email", "eve@example.com"]) == 1
    me = anon.post("/api/auth/login", json={"email": "eve@example.com", "password": "cli-made-pass-1"}).json()
    assert me["admin"] and me["verified"] and me["role"] == "owner"
    assert manage.main(["list-users"]) == 0
    assert "eve@example.com  [admin]" in capsys.readouterr().out
    monkeypatch.setenv("MIXAI_NEW_PASSWORD", "short")
    assert manage.main(["create-user", "--email", "fay@example.com"]) == 1
