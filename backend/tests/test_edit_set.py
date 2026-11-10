"""Image Edit sets: one instruction made into up to 25 separate pictures under one parent job."""
import pytest
from sqlalchemy import select

from app import edit_set, llm
from app.db import SessionLocal
from app.models import Folder, Generation, Job, MediaItem
from app.worker import process_one
from tests.test_api_tokens import _mint, flags, login  # noqa: F401  (fixtures)
from tests.test_library import png_bytes, upload
from tests.test_mcp import Mcp
from tests.test_packages import _spend, ben  # noqa: F401  (fixture)

ASK = "keep everything the same except create a composite sheet of 25 distinct images, each with a unique expression"


class _Res:
    def __init__(self, data):
        self.data = data


@pytest.fixture
def writer(monkeypatch):
    asked = []

    def install(lines=None, fail=False):
        def fake(role, messages, **kw):
            if role == "vision":
                # the picture check on each finished picture; no eye here, so the picture stands
                raise llm.LLMError("no vision model in this test")
            asked.append(messages)
            if fail:
                raise llm.LLMError("the writer is offline")
            return _Res(edit_set._Versions(versions=lines))
        monkeypatch.setattr(llm, "chat_sync", fake)
        return asked
    return install


def _source(http) -> str:
    r = upload(http, "face.png", png_bytes((512, 512)), "image/png")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _make(http, n, **extra):
    r = http.post("/api/images/edit", json={"source_ids": [_source(http)], "instruction": ASK, "set_size": n,
                                            **extra})
    assert r.status_code == 202, r.text
    return r.json()


def _set_rows(job_id):
    with SessionLocal() as db:
        parent = db.get(Job, job_id)
        gens = [db.get(Generation, gid) for gid in parent.payload["generation_ids"]]
        kids = {g.id: db.get(Job, g.job_id) if g.job_id else None for g in gens}
        db.expunge_all()
    return parent, gens, kids


def _drain(fast_driver):
    while process_one(driver_factory=fast_driver):
        pass


def test_a_set_is_one_folder_and_one_job(ben):  # noqa: F811
    _, b = ben
    out = _make(b, 25)
    assert len(out["items"]) == 25 and len(out["jobs"]) == 1
    assert out["jobs"][0]["type"] == "edit_set"
    assert {i["status"] for i in out["items"]} == {"queued"}
    with SessionLocal() as db:
        folders = {i.folder_id for i in db.scalars(select(MediaItem).where(MediaItem.id.in_(
            [i["id"] for i in out["items"]])))}
        assert len(folders) == 1
        f = db.get(Folder, folders.pop())
        assert f.kind == "image" and f.name.startswith("Expressions set · ")
        # nothing on the GPU queue yet: only the parent exists
        assert db.scalars(select(Job.type)).all() == ["edit_set"]
    # Free allows two jobs at once and a set is one of them
    _make(b, 9)
    r = b.post("/api/images/edit", json={"source_ids": [_source(b)], "instruction": ASK, "set_size": 4})
    assert r.status_code == 429 and r.json()["reason"] == "queued"


def test_limits_and_options(client):
    src = _source(client)
    for n in (1, 26):
        r = client.post("/api/images/edit", json={"source_ids": [src], "instruction": ASK, "set_size": n})
        assert r.status_code == 422
    assert client.get("/api/images/options").json()["edit_set"] == {"min": 2, "max": 25}


def test_the_writer_plans_each_picture_and_two_run_at_a_time(client, fast_driver, writer):
    lines = [f"She looks {w}" for w in ("happy", "sad", "angry", "calm", "bored", "shy")]
    asked = writer(["Show the grid of six faces", lines[0], lines[0].upper(), *lines[1:]])
    out = _make(client, 6, seed=100)
    job_id = out["jobs"][0]["id"]

    assert process_one(driver_factory=fast_driver)
    parent, gens, kids = _set_rows(job_id)
    assert len(asked) == 1 and "6" in asked[0][0]["content"]
    assert parent.result["written_by"] == "writer" and parent.status == "queued"
    assert sum(1 for k in kids.values() if k is not None and k.status == "queued") == 2
    assert sum(1 for g in gens if g.job_id is None) == 4

    peak = 0
    while process_one(driver_factory=fast_driver):
        with SessionLocal() as db:
            live = db.scalars(select(Job).where(Job.type == "generate", Job.status.in_(("queued", "running")))).all()
            peak = max(peak, len(live))
    assert peak <= 2
    parent, gens, kids = _set_rows(job_id)
    assert parent.status == "done" and parent.result["done"] == 6 and parent.result["failed"] == 0
    assert all(g.status == "ready" for g in gens)
    assert all(k.payload["parent_job_id"] == job_id for k in kids.values())
    # each picture carries its own line, the same-person sentence and its own seed
    for i, g in enumerate(gens):
        assert g.params["user_prompt"].startswith(lines[i]) and edit_set.SAME in g.prompt
        assert g.seed == 100 + i and g.params["edit_set"]["index"] == i + 1
    # the writer ran once: its lines are on the job
    assert len(asked) == 1


def test_the_lists_stand_in_when_the_writer_fails(client, fast_driver, writer):
    writer(fail=True)
    out = _make(client, 25)
    _drain(fast_driver)
    parent, gens, _ = _set_rows(out["jobs"][0]["id"])
    assert parent.status == "done" and parent.result["written_by"] == "lists"
    prompts = [g.params["user_prompt"] for g in gens]
    assert len(set(prompts)) == 25 and all(g.status == "ready" for g in gens)
    assert not any(edit_set._SHEET_WORDS.search(p) for p in prompts)


def test_a_short_answer_is_topped_up_from_the_lists(writer):
    writer(["She laughs", "She frowns"])
    lines, by = edit_set.write_lines(ASK, 5)
    assert by == "writer+lists" and len(set(lines)) == 5 and lines[0].startswith("She laughs")


def test_out_of_minutes_stops_the_rest_and_keeps_the_finished(ben, fast_driver, writer):  # noqa: F811
    uid, b = ben
    writer(fail=True)
    out = _make(b, 5)
    assert process_one(driver_factory=fast_driver)  # the parent queues the first two
    _spend(uid, 30)
    _drain(fast_driver)
    parent, gens, kids = _set_rows(out["jobs"][0]["id"])
    assert parent.status == "done" and parent.result["stopped"] == "Stopped: out of today's minutes"
    assert [g.status for g in gens] == ["ready", "ready", "failed", "failed", "failed"]
    assert {kids[g.id].error for g in gens[2:]} == {"Stopped: out of today's minutes"}


def test_cancelling_the_set_cancels_the_rest(client, fast_driver, writer):
    writer(fail=True)
    out = _make(client, 4)
    job_id = out["jobs"][0]["id"]
    assert process_one(driver_factory=fast_driver)
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    _drain(fast_driver)
    _, gens, kids = _set_rows(job_id)
    assert all(g.status == "failed" for g in gens)
    assert [k.status for k in kids.values() if k is not None] == ["cancelled", "cancelled"]


def test_mcp_edit_image_makes_a_set(client, anon, writer):
    writer(fail=True)
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    out = m.call_json("edit_image", source_ids=[_source(client)], instruction="nine expressions", set_size=9)
    assert len(out["items"]) == 9 and len(out["jobs"]) == 1 and "get_media" in out["next"]
    assert m.call("edit_image", source_ids=["x"], instruction="y", set_size=30)["isError"]


def test_fallback_lines_never_repeat():
    lines = edit_set.fallback_variants(25)
    assert len(set(lines)) == 25 and len(set(edit_set.EXPRESSIONS)) == 25 and len(edit_set.LIGHTING) == 12
    assert edit_set.set_name("make 9 different camera angles").startswith("Angles set")
    assert edit_set.set_name("a grid of 9 cats in hats").startswith("Cats in hats set · ")


def test_a_sheet_request_also_gets_one_picture_of_them_all(client, fast_driver, writer):
    writer([f"Expression {i}" for i in range(5)])
    out = _make(client, 5, with_sheet=True)
    assert len(out["items"]) == 6 and out["items"][-1]["title"].endswith("· all 5")
    _drain(fast_driver)
    parent, gens, _ = _set_rows(out["jobs"][0]["id"])
    assert parent.status == "done" and all(g.status == "ready" for g in gens)
    with SessionLocal() as db:
        sheet = db.get(Generation, parent.payload["sheet_generation_id"])
        item = db.get(MediaItem, sheet.target_id)
        assert sheet.status == "ready" and sheet.params["pictures"] == 5 and sheet.file_path
        # 5 pictures: 3 across, 2 rows, the same shape as the pictures
        assert item.folder_id == parent.payload["folder_id"] and item.width == edit_set.SHEET_W
    assert client.get(f"/api/media/{sheet.target_id}").status_code == 200
