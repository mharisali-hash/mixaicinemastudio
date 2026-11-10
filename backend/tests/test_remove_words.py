"""Remove by words: SAM finds what the words name, LaMa fills it in, nothing else in the picture changes."""
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select

from app import image_tools as it
from app.config import get_settings
from app.db import SessionLocal
from app.models import Generation, Job
from app.workflows import build
from tests.test_api_tokens import _mint, _user, flags, login  # noqa: F401  (fixtures)
from tests.test_bulk import drain, fill, items, make_batch, roomy_disk  # noqa: F401
from tests.test_image_tools import gen_params
from tests.test_library import png_bytes, upload
from tests.test_mcp import Mcp
from tests.test_storyboard import _run_all


@pytest.fixture
def photo(client):
    return upload(client, "street.png", png_bytes((320, 200)), "image/png").json()


def test_the_graph_finds_the_words_and_fills_them_in():
    graph, _ = build("image_remove_words", {"image": "a.png", "words": "men", "width": 640, "height": 480,
                                            "grow": 12, "threshold": 0.4, "seed": 1})
    by = {n["_meta"]["title"]: n for n in graph.values()}
    fit = next(k for k, n in graph.items() if n["_meta"]["title"] == "Fit")
    assert by["Words"]["inputs"]["text"] == "men"
    # SAM looks at the same working-size picture LaMa fills, so the mask always fits
    assert by["Detect"]["inputs"]["image"] == [fit, 0] and by["Inpaint"]["inputs"]["image"] == [fit, 0]
    assert by["Detect"]["inputs"]["threshold"] == 0.4 and by["Grow"]["inputs"]["expand"] == 12


def test_remove_needs_words(client, photo):
    r = client.post("/api/images/edit", json={"source_ids": [photo["id"]], "tool": "remove"})
    assert r.status_code == 422 and "Say what to remove" in r.json()["detail"]


def test_remove_queues_one_tool_job(client, photo, fast_driver):
    r = client.post("/api/images/edit", json={"source_ids": [photo["id"]], "tool": "remove",
                                              "tool_options": {"words": "  the   men "}})
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["tool_resolved"] == "remove" and len(out["items"]) == 1
    p = gen_params(out["items"][0])
    rec = p["image_tool"]
    assert rec["template"] == "image_remove_words" and rec["inputs"]["words"] == "the men"
    assert rec["inputs"]["grow"] == 10 and "mask_file" not in rec
    with SessionLocal() as db:
        assert db.get(Generation, out["items"][0]["generation_id"]).prompt == "Remove the men"
    _run_all(fast_driver)
    with SessionLocal() as db:
        assert db.get(Generation, out["items"][0]["generation_id"]).status == "ready"


def test_a_child_with_sexual_words_is_refused_as_remove_words_too(client, photo):
    r = client.post("/api/images/edit", json={"source_ids": [photo["id"]], "tool": "remove",
                                              "tool_options": {"words": "the girl's clothes so she is naked"}})
    assert r.status_code == 422 and "child" in r.json()["detail"]


def test_nothing_found_is_said_rather_than_filed(client, photo, fast_driver, monkeypatch):
    r = client.post("/api/images/edit", json={"source_ids": [photo["id"]], "tool": "remove",
                                              "tool_options": {"words": "dragons"}})
    gid = r.json()["items"][0]["generation_id"]
    with SessionLocal() as db:
        src = db.get(Generation, gen_params(r.json()["items"][0])["image_tool"]["source_id"])
        src_path = get_settings().data_dir / src.file_path

    # the mock driver draws a placeholder; here it hands back the picture as it was, as LaMa does on an empty mask
    from app.drivers.mock import MockDriver

    real = MockDriver.generate_image

    def same(self, prompt, params, seed, out_path, progress_cb):
        res = real(self, prompt, params, seed, out_path, progress_cb)
        with Image.open(src_path) as im:
            im.convert("RGB").resize((params["width"], params["height"])).save(out_path)
        return res

    monkeypatch.setattr(MockDriver, "generate_image", same)
    _run_all(fast_driver)
    with SessionLocal() as db:
        g = db.get(Generation, gid)
        j = db.scalars(select(Job).where(Job.generation_id == gid)).one()
        assert g.status == "failed" and "Nothing matching “dragons” was found" in j.error


def test_a_real_change_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    Image.new("RGB", (200, 100), (120, 80, 40)).save(tmp_path / "src.png")
    out = Image.new("RGB", (200, 100), (120, 80, 40))
    out.paste((30, 160, 200), (20, 20, 80, 90))  # a man-sized patch filled with sky
    out.save(tmp_path / "out.png")
    Image.new("RGB", (200, 100), (120, 80, 40)).save(tmp_path / "same.png")
    src = SimpleNamespace(file_path="src.png")
    db = SimpleNamespace(get=lambda _model, gid: src if gid == "g1" else None)
    gen = SimpleNamespace(params={"image_tool": {"tool": "remove", "source_id": "g1", "inputs": {"words": "men"}}})
    assert it.nothing_removed(db, gen, tmp_path / "out.png") is None
    assert "men" in it.nothing_removed(db, gen, tmp_path / "same.png")

def test_bulk_remove_needs_words_then_queues_one_tool_job_each(client, fast_driver, roomy_disk):  # noqa: F811
    bid = make_batch(client, "remove")
    fill(client, bid, 3)
    r = client.post(f"/api/admin/bulk/{bid}/run")
    assert r.status_code == 422 and "Say what to remove" in r.json()["detail"]
    assert client.patch(f"/api/admin/bulk/{bid}", json={"options": {"words": "men"}}).status_code == 200
    assert client.post(f"/api/admin/bulk/{bid}/run").status_code == 200
    drain(fast_driver)
    done = items(bid)
    assert [i.status for i in done] == ["done"] * 3
    with SessionLocal() as db:
        for i in done:
            rec = db.get(Generation, i.result_gen_id).params["image_tool"]
            assert rec["template"] == "image_remove_words" and rec["inputs"]["words"] == "men"
            assert i.prompt_used == "Remove men"


def test_mcp_edit_image_remove(client, anon, photo, fast_driver):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    out = m.call_json("edit_image", source_ids=[photo["id"]], remove="men")
    assert len(out["items"]) == 1
    p = gen_params(out["items"][0])
    assert p["image_tool"]["tool"] == "remove" and p["image_tool"]["inputs"]["words"] == "men"
    res = m.call("edit_image", source_ids=[photo["id"]])
    assert res["isError"] and "Say what to change" in res["content"][0]["text"]
