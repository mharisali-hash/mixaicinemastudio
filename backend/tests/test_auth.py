from tests.conftest import ADMIN


def test_health_needs_no_session(anon):
    assert anon.get("/api/health").json() == {"status": "ok"}


def test_protected_routes_401_without_session(anon):
    for path in ("/api/auth/me", "/api/projects", "/api/jobs", "/api/generations", "/api/system/status"):
        r = anon.get(path)
        assert r.status_code == 401, path
        assert "detail" in r.json()


def test_login_me_logout(anon):
    r = anon.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == ADMIN["email"]
    assert body["role"] == "owner"
    assert set(body) == {"id", "email", "display_name", "workspace_id", "role", "api_access", "api_docs", "admin",
                         "verified", "implant", "staff", "blocked"}
    assert body["staff"] == "admin" and body["blocked"] is None

    cookie = r.headers["set-cookie"].lower()
    assert "mixai_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie

    assert anon.get("/api/auth/me").json()["id"] == body["id"]

    assert anon.post("/api/auth/logout").status_code == 204
    assert anon.get("/api/auth/me").status_code == 401


def test_bad_password_is_401(anon):
    r = anon.post("/api/auth/login", json={"email": ADMIN["email"], "password": "nope"})
    assert r.status_code == 401
    r = anon.post("/api/auth/login", json={"email": "who@else", "password": "x"})
    assert r.status_code == 401


def test_tampered_cookie_rejected(anon):
    anon.cookies.set("mixai_session", "garbage.value.here")
    assert anon.get("/api/auth/me").status_code == 401
