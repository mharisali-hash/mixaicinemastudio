"""Rights (contract v42): release, packages and per-user overrides, enforced on jobs, routes, models and MCP."""
from sqlalchemy import delete, select

from app import models_catalog as mc
from app import rights
from app.db import SessionLocal
from app.models import RightRelease, User
from tests.test_api_tokens import _bearer, _mint, flags, login  # noqa: F401  (fixtures)
from tests.test_mcp import Mcp
from tests.test_packages import KITE, ben  # noqa: F401


def _unrelease(kind: str, key: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(RightRelease).where(RightRelease.kind == kind, RightRelease.key == key))
        db.commit()


def _image_models() -> tuple[str, str]:
    default = mc.default_for("image").id
    other = next(m.id for m in mc.MODELS.values() if m.type == "image" and m.id != default and not mc.hidden(m))
    return default, other


def test_baseline_releases_todays_studios_and_models_but_not_print():
    with SessionLocal() as db:
        rel = rights.released(db)
    assert {"image", "video", "podcast", "api", "song_voices"} <= rel["feature"]
    assert not {"print", "presenter", "bulk_edit", "implant"} & rel["feature"]
    assert set(mc.MODELS) == rel["model"]


def test_layers_admin_override_release_package(client, ben):  # noqa: F811
    uid, b = ben
    with SessionLocal() as db:
        r = rights.effective(db, db.get(User, uid))
        assert r.allows("image") and not r.allows("print") and not r.allows("implant")
        admin = db.scalars(select(User).where(User.is_admin.is_(True))).first()
        assert rights.effective(db, admin).allows("print")

    # an override opens an unreleased studio for one person, and closes a released one
    r = client.put(f"/api/admin/users/{uid}/rights", json={"features": {"print": True, "podcast": False}})
    assert r.status_code == 200, r.text
    assert r.json()["features"]["print"] is True and r.json()["features"]["podcast"] is False
    usage = b.get("/api/account/usage").json()
    assert usage["features"]["print"] is True and usage["features"]["podcast"] is False

    # null drops the override and the plan decides again
    client.put(f"/api/admin/users/{uid}/rights", json={"features": {"print": None}})
    assert b.get("/api/account/usage").json()["features"]["print"] is False
    assert client.put(f"/api/admin/users/{uid}/rights", json={"features": {"nope": True}}).status_code == 422
    assert client.put(f"/api/admin/users/{uid}/rights", json={"features": {"implant": True}}).status_code == 422


def test_unreleased_model_is_off_until_released_or_overridden(client, ben):  # noqa: F811
    uid, b = ben
    _, other = _image_models()
    _unrelease("model", other)
    assert other not in [m["id"] for m in b.get("/api/models?type=image").json()]
    assert other in [m["id"] for m in client.get("/api/models?type=image").json()]
    r = b.post("/api/images/generate", json={**KITE, "model": other})
    assert r.status_code == 403 and other in r.json()["detail"]
    assert client.post("/api/images/generate", json={**KITE, "model": other}).status_code == 202

    client.put(f"/api/admin/users/{uid}/rights", json={"models": {other: True}})
    assert b.post("/api/images/generate", json={**KITE, "model": other}).status_code == 202
    client.put(f"/api/admin/users/{uid}/rights", json={"models": {}})
    assert b.post("/api/images/generate", json={**KITE, "model": other}).status_code == 403

    overview = client.get("/api/admin/rights").json()
    assert next(m for m in overview["models"] if m["key"] == other)["released"] is False
    assert client.post("/api/admin/rights/release", json={"kind": "model", "key": other}).status_code == 200
    assert b.post("/api/images/generate", json={**KITE, "model": other}).status_code == 202
    assert client.post("/api/admin/rights/release", json={"kind": "model", "key": "nope"}).status_code == 422


def test_auto_only_picks_what_the_plan_has(client, ben):  # noqa: F811
    uid, b = ben
    default, other = _image_models()
    client.put(f"/api/admin/users/{uid}/rights", json={"models": {default: False}})
    r = b.post("/api/images/generate", json={**KITE, "model": "auto"})
    assert r.status_code == 202, r.text
    with SessionLocal() as db:
        db.info["acting_user"] = uid
        m, params = rights.choose(db, "auto", "image", "a red kite")
    assert m.id != default and params["model_requested"] == "auto"


def test_switched_off_studio_refuses_changes_but_still_reads(client, ben):  # noqa: F811
    uid, b = ben
    client.put(f"/api/admin/users/{uid}/rights", json={"features": {"poetry": False}})
    assert b.get("/api/poetry").status_code == 200
    r = b.post("/api/poetry", json={"title": "x"})
    assert r.status_code == 403 and "Poetry" in r.json()["detail"]


def test_print_stays_hidden_until_opened(client, ben):  # noqa: F811
    uid, b = ben
    assert b.get("/api/print").status_code == 404
    client.put(f"/api/admin/users/{uid}/rights", json={"features": {"print": True}})
    assert b.get("/api/print").status_code == 200


def test_mcp_hides_and_refuses_tools_of_a_switched_off_studio(client, ben, anon, flags):  # noqa: F811
    uid, b = ben
    flags(public_api=True)
    token = _mint(b, ["read", "generate"])["token"]
    names = Mcp(anon, token).tools()
    assert "generate_image" in names and "make_print_design" not in names
    client.put(f"/api/admin/users/{uid}/rights", json={"features": {"image": False}})
    mcp = Mcp(anon, token)
    assert "generate_image" not in mcp.tools()
    res = mcp.call("generate_image", prompt="a red kite")
    assert res["isError"] and "Unknown tool" in res["content"][0]["text"]
    assert b.post("/api/images/generate", json=KITE).status_code == 403


def test_rights_admin_is_admin_only(ben):  # noqa: F811
    uid, b = ben
    assert b.get("/api/admin/rights").status_code == 404
    assert b.put(f"/api/admin/users/{uid}/rights", json={"features": {"print": True}}).status_code == 404
