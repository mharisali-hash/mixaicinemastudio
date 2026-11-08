"""Admin bulk runs (contract v42) on the mock driver."""
import csv
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from app import worker
from app.bulk import service as bs
from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminAudit, BulkItem, Job, MediaItem
from tests.test_api_tokens import _user, login  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch):
    # the dev machine's disk may well be under the real 20 GB reserve
    monkeypatch.setattr(get_settings(), "bulk_disk_reserve_gb", 0)


def png(seed: int = 0, w: int = 160, h: int = 120) -> bytes:
    rng = np.random.default_rng(seed)
    arr = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def make_batch(client, action="edit", **options) -> str:
    if action == "edit":
        options.setdefault("instruction", "turn the sky orange")
    r = client.post("/api/admin/bulk", json={"name": "Shoes", "action": action, "options": options})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def send(client, bid: str, name: str, data: bytes):
    return client.post(f"/api/admin/bulk/{bid}/files", params={"name": name},
                       files={"file": (name.rsplit("/", 1)[-1], data, "image/png")})


def fill(client, bid: str, n: int) -> list[str]:
    names = [f"shoes/red/{i:03d}.png" for i in range(n)]
    for i, name in enumerate(names):
        r = send(client, bid, name, png(i))
        assert r.status_code == 201, r.text
    return names


def drain(fast_driver, check=None):
    while worker.process_one(driver_factory=fast_driver):
        if check:
            check()


def items(bid: str) -> list[BulkItem]:
    with SessionLocal() as db:
        rows = db.scalars(select(BulkItem).where(BulkItem.batch_id == bid).order_by(BulkItem.seq)).all()
        db.expunge_all()
    return rows


def live_children(bid: str) -> int:
    with SessionLocal() as db:
        return sum(1 for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running")), Job.type == "generate"))
                   if (j.payload or {}).get("bulk_batch_id") == bid)


def test_admins_only(client, login):  # noqa: F811
    editor = login(_user("ed@test.local", "editor"))
    assert editor.get("/api/admin/bulk").status_code == 404
    assert editor.post("/api/admin/bulk", json={"action": "cutout"}).status_code == 404
    bid = make_batch(client)
    assert editor.post(f"/api/admin/bulk/{bid}/run").status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(AdminAudit).where(AdminAudit.action == "bulk_create")).first() is not None


def test_upload_resumes_and_stays_out_of_the_studio(client):
    bid = make_batch(client)
    names = fill(client, bid, 3)
    # the same file again (a retried request) is not stored twice
    r = send(client, bid, names[0], png(0))
    assert r.status_code == 201 and r.json()["duplicate"] is True
    # a reselected folder: the page asks what is already there and sends only the rest
    have = client.post(f"/api/admin/bulk/{bid}/check", json={"files": [
        {"name": names[0], "size": len(png(0))}, {"name": "shoes/red/new.png", "size": 10},
        {"name": names[1], "size": 1}]}).json()["have"]
    assert have == [names[0]]
    assert [i.filename for i in items(bid)] == names
    # nothing of the batch reaches the admin's own Library or job tray
    assert client.get("/api/media").json()["items"] == []
    assert client.get("/api/jobs").json() == []


def test_upload_takes_pictures_only_and_keeps_free_disk(client, monkeypatch):
    bid = make_batch(client)
    r = client.post(f"/api/admin/bulk/{bid}/files", params={"name": "a.wav"},
                    files={"file": ("a.wav", b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 64, "audio/wav")})
    assert r.status_code == 415
    monkeypatch.setattr(get_settings(), "bulk_disk_reserve_gb", 10**9)
    r = send(client, bid, "x.png", png())
    assert r.status_code == 507 and "free" in r.json()["detail"]


def test_run_keeps_two_in_flight_and_finishes(client, fast_driver):
    bid = make_batch(client)
    fill(client, bid, 5)
    assert client.post(f"/api/admin/bulk/{bid}/run").json()["status"] == "running"
    seen = []
    drain(fast_driver, lambda: seen.append(live_children(bid)))
    assert max(seen) <= get_settings().bulk_in_flight
    out = client.get(f"/api/admin/bulk/{bid}").json()
    assert out["status"] == "done" and out["counts"]["done"] == 5
    for i in items(bid):
        assert i.result_gen_id and i.prompt_used and i.attempts == 1
    with SessionLocal() as db:
        children = [j for j in db.scalars(select(Job).where(Job.type == "generate"))]
        assert {j.priority for j in children} == {bs.BULK_PRIORITY}
        assert {j.user_id for j in children} == {db.scalars(select(Job.user_id).where(Job.type == bs.RUN_JOB)).one()}
        # edit results are new pictures in the hidden workspace, never in the admin's Library
        assert {m.workspace_id for m in db.scalars(select(MediaItem))} == {bs._WS_ID}


def test_any_new_request_goes_before_the_next_bulk_picture(client, fast_driver):
    """The owner's rule: an app, MCP or API request made mid-batch, the admin's own included, is served first."""
    bid = make_batch(client)
    fill(client, bid, 4)
    client.post(f"/api/admin/bulk/{bid}/run")
    assert worker.process_one(driver_factory=fast_driver)  # the orchestrator queues its first pictures
    assert live_children(bid) == 2
    with SessionLocal() as db:
        ws = db.scalars(select(Job.workspace_id).where(Job.type == "generate")).first()
        mine = Job(workspace_id=ws, type="generate", lane="image", priority=0, message="the admin's MCP request")
        db.add(mine)
        db.commit()
        claimed = worker.claim_next(db)
        assert claimed.id == mine.id
        claimed.status = "done"
        db.commit()
        nxt = worker.claim_next(db)
        assert (nxt.payload or {}).get("bulk_batch_id") == bid
        nxt.status = "queued"
        db.commit()
    drain(fast_driver)
    assert client.get(f"/api/admin/bulk/{bid}").json()["counts"]["done"] == 4


def test_pause_resume_cancel_and_retry(client, fast_driver):
    bid = make_batch(client)
    fill(client, bid, 4)
    client.post(f"/api/admin/bulk/{bid}/run")
    worker.process_one(driver_factory=fast_driver)
    paused = client.post(f"/api/admin/bulk/{bid}/pause").json()
    assert paused["status"] == "paused" and paused["counts"]["uploaded"] == 4 and live_children(bid) == 0
    drain(fast_driver)
    assert client.get(f"/api/admin/bulk/{bid}").json()["counts"]["done"] == 0
    client.post(f"/api/admin/bulk/{bid}/run")
    drain(fast_driver)
    assert client.get(f"/api/admin/bulk/{bid}").json()["counts"]["done"] == 4

    with SessionLocal() as db:
        it = db.scalars(select(BulkItem).where(BulkItem.batch_id == bid)).first()
        it.status, it.error = "failed", "GPU out of memory"
        db.commit()
    out = client.post(f"/api/admin/bulk/{bid}/retry-failed").json()
    assert out["status"] == "running"
    drain(fast_driver)
    assert client.get(f"/api/admin/bulk/{bid}").json()["counts"]["done"] == 4

    bid2 = make_batch(client)
    fill(client, bid2, 3)
    client.post(f"/api/admin/bulk/{bid2}/run")
    worker.process_one(driver_factory=fast_driver)
    out = client.post(f"/api/admin/bulk/{bid2}/cancel").json()
    assert out["status"] == "cancelled" and live_children(bid2) == 0
    drain(fast_driver)
    assert client.get(f"/api/admin/bulk/{bid2}").json()["counts"]["done"] == 0


def test_preview_runs_three_at_normal_priority(client, fast_driver):
    bid = make_batch(client, action="cutout")
    fill(client, bid, 5)
    picks = client.post(f"/api/admin/bulk/{bid}/preview").json()["items"]
    assert len(picks) == 3
    with SessionLocal() as db:
        assert {j.priority for j in db.scalars(select(Job).where(Job.type == "generate"))} == {0}
    drain(fast_driver)
    got = client.get(f"/api/admin/bulk/{bid}/items", params={"status": "samples"}).json()["items"]
    assert [i["status"] for i in got] == ["done"] * 3 and all(i["result_url"] for i in got)
    assert client.get(got[0]["result_url"], params={"w": 256}).status_code == 200
    # new settings: the samples are made again rather than mixed into the run
    client.patch(f"/api/admin/bulk/{bid}", json={"options": {"feather": 3}})
    assert client.get(f"/api/admin/bulk/{bid}").json()["counts"]["uploaded"] == 5


def test_revered_figures_are_refused_even_for_admins(client):
    # the writer model is unreachable in tests, and no answer means no
    r = client.post("/api/admin/bulk", json={"action": "edit",
                                             "options": {"instruction": "a portrait of the Prophet Muhammad"}})
    assert r.status_code == 422
    bid = make_batch(client)
    fill(client, bid, 1)
    data = "filename,instruction\n000.png,draw Prophet Muhammad on a horse\n".encode()
    r = client.post(f"/api/admin/bulk/{bid}/prompts", files={"file": ("p.csv", data, "text/csv")})
    assert r.status_code == 422


def test_prompts_csv_and_export(client, fast_driver):
    bid = make_batch(client, keywords="product, studio")
    names = fill(client, bid, 3)
    data = ("filename,instruction,keywords\n"
            f"{names[0]},make the shoe blue,shoe, blue\n"
            "001.png,make it gold,\n"
            "missing.png,whatever,\n").encode()
    r = client.post(f"/api/admin/bulk/{bid}/prompts", files={"file": ("p.csv", data, "text/csv")}).json()
    assert r["matched"] == 2 and r["unmatched"] == ["missing.png"]
    client.post(f"/api/admin/bulk/{bid}/run")
    drain(fast_driver)
    with SessionLocal() as db:
        it = db.scalars(select(BulkItem).where(BulkItem.batch_id == bid, BulkItem.seq == 1)).one()
        assert it.instruction == "make the shoe blue"
    job_id = client.post(f"/api/admin/bulk/{bid}/export", json={"originals": True}).json()["job_id"]
    drain(fast_driver)
    ex = client.get(f"/api/admin/bulk/{bid}/exports").json()["exports"][0]
    assert ex["job_id"] == job_id and ex["status"] == "done" and len(ex["parts"]) == 1
    r = client.get(ex["parts"][0]["url"])
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    listed = set(z.namelist())
    assert {"images/shoes/red/000.png", "images/shoes/red/000.txt", "originals/shoes/red/000.png",
            "metadata.jsonl", "captions.csv"} <= listed
    rows = [json.loads(line) for line in z.read("metadata.jsonl").decode().splitlines()]
    first = next(r for r in rows if r["original_filename"] == names[0])
    assert first["instruction"] == "make the shoe blue" and first["keywords"] == "shoe"
    gold = next(r for r in rows if r["original_filename"] == names[1])
    assert gold["instruction"] == "make it gold" and gold["keywords"] == "product, studio"
    assert z.read("images/shoes/red/001.txt").decode() == "make it gold, product, studio"
    table = list(csv.DictReader(io.StringIO(z.read("captions.csv").decode("utf-8-sig"))))
    assert len(table) == 3 and table[0]["prompt_used"]


def test_zip_upload_and_delete(client, fast_driver):
    bid = make_batch(client, action="cutout")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("set/a.png", png(1))
        z.writestr("set/b.png", png(2))
        z.writestr("__MACOSX/set/._a.png", b"junk")
        z.writestr("notes.txt", b"not a picture")
    r = client.post(f"/api/admin/bulk/{bid}/zip", files={"file": ("set.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 202, r.text
    drain(fast_driver)
    assert [i.filename for i in items(bid)] == ["set/a.png", "set/b.png"]
    client.post(f"/api/admin/bulk/{bid}/run")
    drain(fast_driver)
    assert client.get(f"/api/admin/bulk/{bid}").json()["counts"]["done"] == 2
    assert client.delete(f"/api/admin/bulk/{bid}").status_code == 204
    assert client.get(f"/api/admin/bulk/{bid}").status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(MediaItem)).all() == []
