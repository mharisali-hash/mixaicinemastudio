"""Moderation (contract v42): managers and admins review, remove, warn and hold accounts; the person sees it."""
import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AdminAudit, Job, MediaItem, User, UserNotice
from tests.test_accounts import PW, make_user
from tests.test_api_tokens import _bearer, _mint, flags, login  # noqa: F401  (fixtures)
from tests.test_mcp import Mcp


def _png() -> io.BytesIO:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "PNG")
    buf.seek(0)
    return buf


def _upload(c, name="red.png") -> dict:
    r = c.post("/api/media/upload", files={"file": (name, _png(), "image/png")})
    assert r.status_code in (200, 201), r.text
    item = r.json()
    with SessionLocal() as db:
        # uploads skip the queue; these stand in for something the person generated
        db.get(MediaItem, item["id"]).origin = "generated"
        db.commit()
    return item


@pytest.fixture
def people(client, login):  # noqa: F811
    """Ben and Cara are ordinary accounts; Mo is a whole-site manager, Tess a team manager for Cara only."""
    ids = {n: make_user(f"{n}@example.com", n.title()) for n in ("ben", "cara", "mo", "tess")}
    assert client.put(f"/api/admin/users/{ids['mo']}/staff", json={"staff_role": "manager"}).status_code == 200
    r = client.put(f"/api/admin/users/{ids['tess']}/staff",
                   json={"staff_role": "manager", "manager_scope": "team", "team": [ids["cara"]]})
    assert r.status_code == 200, r.text
    clients = {n: login({"email": f"{n}@example.com", "password": PW}) for n in ids}
    return ids, clients


def test_managers_reach_and_what_they_cant_open(client, people):
    ids, c = people
    ben_item, cara_item = _upload(c["ben"]), _upload(c["cara"])
    _upload(c["mo"])  # a manager's own work never shows to another manager

    seen = {i["id"] for i in c["mo"].get("/api/moderation/items").json()["items"]}
    assert {ben_item["id"], cara_item["id"]} <= seen and len(seen) == 2
    team = c["tess"].get("/api/moderation/items").json()["items"]
    assert [i["id"] for i in team] == [cara_item["id"]] and team[0]["owner"]["email"] == "cara@example.com"
    assert c["tess"].post(f"/api/moderation/items/{ben_item['id']}/remove", json={"reason": "spam"}).status_code == 404
    assert c["tess"].post(f"/api/moderation/users/{ids['ben']}/warn", json={"reason": "spam"}).status_code == 404
    assert c["tess"].post(f"/api/moderation/users/{ids['mo']}/block", json={"reason": "nope"}).status_code == 404
    admin_id = client.get("/api/auth/me").json()["id"]
    assert c["mo"].post(f"/api/moderation/users/{admin_id}/warn", json={"reason": "nope"}).status_code == 404

    assert c["ben"].get("/api/moderation/items").status_code == 404
    for path in ("/api/admin/packages", "/api/admin/rights", "/api/admin/users", "/api/admin/payments"):
        assert c["mo"].get(path).status_code == 404, path
    assert c["mo"].get("/api/auth/me").json()["staff"] == "manager"

    # the thumbnail comes through the moderation route, across workspaces
    assert c["mo"].get(team[0]["thumb_url"]).status_code == 200
    assert c["tess"].get(f"/api/moderation/items/{ben_item['id']}/thumb").status_code == 404


def test_remove_hides_the_item_and_tells_the_owner(people):
    ids, c = people
    item = _upload(c["ben"])
    gen_id = item["generation_id"]
    assert c["ben"].get(f"/api/media/thumb/{gen_id}").status_code == 200

    r = c["mo"].post(f"/api/moderation/items/{item['id']}/remove",
                     json={"reason": "Not allowed here", "warn": True})
    assert r.status_code == 200 and r.json()["removed_at"]
    listed = {i["id"] for i in c["ben"].get("/api/media").json()["items"]}
    assert item["id"] not in listed
    assert c["ben"].get(f"/api/media/{item['id']}").status_code == 404
    assert c["ben"].get(f"/api/media/thumb/{gen_id}").status_code == 404

    notices = c["ben"].get("/api/account/notices").json()
    assert notices[0]["kind"] == "warning" and notices[0]["reason"] == "Not allowed here"
    assert c["ben"].post(f"/api/account/notices/{notices[0]['id']}/ack").status_code == 204
    assert c["ben"].get("/api/account/notices").json() == []
    assert c["cara"].post(f"/api/account/notices/{notices[0]['id']}/ack").status_code == 404

    removed = c["mo"].get("/api/moderation/items?state=removed").json()["items"]
    assert [i["id"] for i in removed] == [item["id"]]
    assert c["mo"].post(f"/api/moderation/items/{item['id']}/restore").status_code == 200
    assert item["id"] in {i["id"] for i in c["ben"].get("/api/media").json()["items"]}
    with SessionLocal() as db:
        actions = set(db.scalars(select(AdminAudit.action).where(AdminAudit.action.like("moderation_%"))))
    assert {"moderation_remove", "moderation_warning", "moderation_restore"} <= actions


def test_reviewed_leaves_the_queue(people):
    ids, c = people
    a, b = _upload(c["ben"]), _upload(c["ben"], "b.png")
    assert c["mo"].post("/api/moderation/items/reviewed", json={"ids": [a["id"], "nope"]}).json() == {"reviewed": 1}
    queue = {i["id"] for i in c["mo"].get("/api/moderation/items").json()["items"]}
    assert queue == {b["id"]}
    filtered = c["mo"].get(f"/api/moderation/items?state=reviewed&user={ids['ben']}").json()["items"]
    assert [i["id"] for i in filtered] == [a["id"]]


def test_hold_is_read_only_everywhere_until_lifted(people, anon, flags):  # noqa: F811
    ids, c = people
    flags(public_api=True)
    token = _mint(c["ben"], ["read", "generate"])["token"]
    item = _upload(c["ben"])
    with SessionLocal() as db:
        ws = db.get(MediaItem, item["id"]).workspace_id
        db.add(Job(workspace_id=ws, user_id=ids["ben"], type="ai_logline", status="queued"))
        db.commit()

    for _ in range(2):
        assert c["mo"].post(f"/api/moderation/users/{ids['ben']}/warn", json={"reason": "Be careful"}).status_code == 201
    people_list = c["mo"].get("/api/moderation/users").json()
    assert people_list["warn_limit"] == 3
    assert next(u for u in people_list["users"] if u["id"] == ids["ben"])["warnings"] == 2

    r = c["mo"].post(f"/api/moderation/users/{ids['ben']}/block", json={"reason": "Repeated spam"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert db.scalars(select(Job.status).where(Job.user_id == ids["ben"])).all() == ["cancelled"]

    me = c["ben"].get("/api/auth/me").json()
    assert me["blocked"]["reason"] == "Repeated spam"
    assert c["ben"].get("/api/media").status_code == 200
    r = c["ben"].post("/api/images/generate", json={"prompt": "a kite"})
    assert r.status_code == 403 and "Repeated spam" in r.json()["detail"]
    assert c["ben"].post("/api/media/upload", files={"file": ("x.png", _png(), "image/png")}).status_code == 403
    notices = c["ben"].get("/api/account/notices").json()
    assert all(n["kind"] != "block" for n in notices)
    assert c["ben"].post(f"/api/account/notices/{notices[0]['id']}/ack").status_code == 204
    r = anon.get("/api/media", headers=_bearer(token))
    assert r.status_code == 403 and "on hold" in r.json()["detail"]
    r = Mcp(anon, token).raw("tools/list")
    assert r.status_code == 403 and "Repeated spam" in r.json()["detail"]
    assert c["ben"].post("/api/auth/logout").status_code in (200, 204)

    assert c["mo"].post(f"/api/moderation/users/{ids['ben']}/unblock", json={}).status_code == 200
    assert anon.get("/api/media", headers=_bearer(token)).status_code == 200
    with SessionLocal() as db:
        user = db.get(User, ids["ben"])
        assert user.blocked_at is None
        kinds = db.scalars(select(UserNotice.kind).where(UserNotice.user_id == ids["ben"])).all()
        assert set(kinds) == {"warning", "block", "unblock"}


def test_moderation_log_is_scoped_for_a_team_manager(people):
    ids, c = people
    c["mo"].post(f"/api/moderation/users/{ids['ben']}/warn", json={"reason": "one"})
    c["tess"].post(f"/api/moderation/users/{ids['cara']}/warn", json={"reason": "two"})
    assert {e["target_id"] for e in c["tess"].get("/api/moderation/log").json()} == {ids["cara"]}
    assert {e["target_id"] for e in c["mo"].get("/api/moderation/log").json()} == {ids["ben"], ids["cara"]}


def test_staff_role_is_admin_only_and_checked(client, people):
    ids, c = people
    assert c["mo"].put(f"/api/admin/users/{ids['ben']}/staff", json={"staff_role": "manager"}).status_code == 404
    r = client.put(f"/api/admin/users/{ids['tess']}/staff",
                   json={"staff_role": "manager", "manager_scope": "team", "team": ["nope"]})
    assert r.status_code == 422
    admin_id = client.get("/api/auth/me").json()["id"]
    assert client.put(f"/api/admin/users/{admin_id}/staff", json={"staff_role": "manager"}).status_code == 422
