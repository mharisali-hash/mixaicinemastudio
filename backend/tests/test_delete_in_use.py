"""A picture can't be deleted while an edit made from it is still waiting in the queue."""
from app.db import SessionLocal
from app.models import Job
from tests.test_camera_mentions import upload
from tests.test_storyboard import _run_all


def test_in_use_picture_waits_until_the_edit_is_done(client, fast_driver):
    src = upload(client)
    r = client.post("/api/images/edit", json={"source_ids": [src["id"]], "instruction": "add a red hat"})
    assert r.status_code == 202, r.text

    gone = client.delete(f"/api/media/{src['id']}")
    assert gone.status_code == 409 and "still being made" in gone.json()["detail"]
    batch = client.post("/api/media/batch", json={"action": "delete", "refs": [src["id"]]})
    assert batch.status_code == 200, batch.text
    assert batch.json()["done"] == [] and "still being made" in batch.json()["skipped"][0]["reason"]

    _run_all(fast_driver)
    with SessionLocal() as db:
        assert not db.query(Job).filter(Job.status.in_(("queued", "running"))).count()
    assert client.delete(f"/api/media/{src['id']}").status_code == 204
