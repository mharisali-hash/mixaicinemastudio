"""Held by the age check: only an admin sees it, and any admin (the one who made it too) can let it through."""
import pytest
from sqlalchemy import select

from app import content_policy as cp
from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminAudit, BulkBatch, BulkItem, Generation, Job, MediaItem, User
from tests.test_child_safety import eye  # noqa: F401  (fixture)
from tests.test_content_policy import _last, _run
from tests.test_implant import _client


@pytest.fixture
def second_admin(client):
    with _client("ali@example.com", admin=True) as c:
        yield c


def _held(c, fast_driver, eye_, prompt="a woman at the beach"):
    eye_(minor=True, age=0)
    assert c.post("/api/images/generate", json={"prompt": prompt}).status_code == 202
    _run(fast_driver)
    g, j = _last(c)
    assert g.status == "held" and j.error == cp.HELD_MSG
    return g


def test_release_into_the_library(client, second_admin, fast_driver, eye):  # noqa: F811
    g = _held(client, fast_driver, eye)
    listed = client.get("/api/moderation/held").json()["items"]
    assert [h["id"] for h in listed] == [g.id] and listed[0]["age"] == 0 and listed[0]["why"] == "test"
    assert client.get(listed[0]["file_url"]).status_code == 200

    assert second_admin.post(f"/api/moderation/held/{g.id}/release").status_code == 200

    with SessionLocal() as db:
        g = db.get(Generation, g.id)
        assert g.status == "ready" and g.file_path and "held" not in g.params
        assert (get_settings().data_dir / g.file_path).is_file()
        assert db.get(MediaItem, g.target_id).generation_id == g.id
        assert db.scalar(select(Job).where(Job.generation_id == g.id)).status == "done"
        row = db.scalar(select(AdminAudit).where(AdminAudit.action == "child_check_released"))
        assert row.target_id == g.id and row.actor_email == "ali@example.com"
    assert client.get("/api/moderation/held").json()["items"] == []
    assert client.get(f"/api/media/{g.target_id}").json()["media_url"]


def test_delete_removes_the_file(client, second_admin, fast_driver, eye):  # noqa: F811
    g = _held(client, fast_driver, eye)
    held = get_settings().data_dir / g.params["held"]["path"]
    # deleting needs no second admin: it only takes something away
    assert client.post(f"/api/moderation/held/{g.id}/delete").status_code == 200
    assert not held.exists()
    with SessionLocal() as db:
        g = db.get(Generation, g.id)
        assert g.status == "failed" and g.file_path is None
        assert db.scalar(select(AdminAudit).where(AdminAudit.action == "child_check_deleted")).target_id == g.id
    assert second_admin.post(f"/api/moderation/held/{g.id}/release").status_code == 404


def test_managers_and_members_never_see_it(client, fast_driver, eye):  # noqa: F811
    g = _held(client, fast_driver, eye)
    manager = _client("mo@example.com")
    with SessionLocal() as db:
        db.scalar(select(User).where(User.email == "mo@example.com")).staff_role = "manager"
        db.commit()
    member = _client("sana@example.com")
    for c in (manager, member):
        assert c.get("/api/moderation/held").status_code == 404
        assert c.get(f"/api/moderation/held/{g.id}/file").status_code == 404
        assert c.post(f"/api/moderation/held/{g.id}/release").status_code == 404


def test_an_admin_releases_their_own_and_the_bulk_item_takes_it(client, fast_driver, eye):  # noqa: F811
    g = _held(client, fast_driver, eye)
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.generation_id == g.id))
        batch = BulkBatch(workspace_id=g.workspace_id, name="b", action="edit", status="done")
        db.add(batch)
        db.flush()
        item = BulkItem(batch_id=batch.id, filename="a.jpg", job_id=job.id, status="failed", error=cp.HELD_MSG)
        db.add(item)
        db.commit()
        item_id = item.id

    assert client.post(f"/api/moderation/held/{g.id}/release").status_code == 200
    with SessionLocal() as db:
        item = db.get(BulkItem, item_id)
        assert item.status == "done" and item.result_gen_id == g.id and item.error is None
        row = db.scalar(select(AdminAudit).where(AdminAudit.action == "child_check_released"))
        assert row.detail["own"] is True and row.detail["confirmed_adult"] is True
