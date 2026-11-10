"""Image → Create batches: up to 8 pictures from one ComfyUI run on the fast models (app.image_batch)."""
from pathlib import Path

import httpx
import pytest

from app import content_policy, thumbs
from app.config import get_settings
from app.db import SessionLocal
from app.drivers.comfy import ComfyDriver, plan_generation
from app.drivers.comfy_client import ComfyClient, ComfyError
from app.drivers.mock import MockDriver
from app.models import Generation, Job, MediaItem
from app.workflows import build, find_node
from tests.test_storyboard import _run_all

OOM = "ComfyUI KSampler (node 8) failed: CUDA out of memory. Tried to allocate 2.00 GiB"


def _make(client, **body):
    r = client.post("/api/images/generate", json={"prompt": "a lighthouse at dusk", **body})
    assert r.status_code == 202, r.text
    return r.json()


def _gens(db, body) -> list[Generation]:
    return [db.get(Generation, i["generation_id"]) for i in body["items"]]


class Recorder(MockDriver):
    def __init__(self, fail=None):
        super().__init__(step_seconds=0)
        self.calls, self.fail = [], fail

    def generate_image(self, prompt, params, seed, out_path, progress_cb):
        self.calls.append({"seed": seed, "index": params.get("batch_index", 0),
                           "size": 1 + len(params.get("batch_out") or [])})
        if self.fail and self.fail(params):
            raise ComfyError(OOM)
        res = super().generate_image(prompt, params, seed, out_path, progress_cb)
        # what the comfy driver reports: one GPU time for the whole run
        res.params_update = {"comfy": {"seed": seed, "gpu_seconds": 8.0, "inputs": {"batch_index": 0}}}
        res.meta["gpu_seconds"] = 8.0
        return res


@pytest.fixture
def checked(monkeypatch):
    seen = []

    def check(ctx, gen, path, media_type):
        seen.append(gen.id)

    # the test client is an admin, whom the real check skips
    monkeypatch.setattr(content_policy, "check_after", check)
    return seen


def test_eight_on_a_fast_model_are_one_run(client, checked):
    body = _make(client, count=8, seed=1234)
    assert len(body["items"]) == 8 and len(body["jobs"]) == 1
    with SessionLocal() as db:
        gens = _gens(db, body)
        lead = gens[0]
        assert lead.params["batch"] == {"ids": [g.id for g in gens], "index": 0}
        assert [g.params["batch"]["index"] for g in gens[1:]] == list(range(1, 8))
        assert {g.seed for g in gens} == {1234} and all(g.job_id is None for g in gens[1:])

    d = Recorder()
    _run_all(lambda: d)
    assert d.calls == [{"seed": 1234, "index": 0, "size": 8}]
    with SessionLocal() as db:
        gens = _gens(db, body)
        assert {g.status for g in gens} == {"ready"} and len({g.file_path for g in gens}) == 8
        # every picture had its own content check
        assert sorted(checked) == sorted(g.id for g in gens)
        assert all(g.prompt == gens[0].prompt for g in gens)
        # billed once on the job, a share on each picture's record
        assert db.get(Job, gens[0].job_id).gpu_seconds == 8.0
        assert [g.params["comfy"]["gpu_seconds"] for g in gens] == [1.0] * 8
        assert [g.params["comfy"]["inputs"]["batch_index"] for g in gens] == list(range(8))
        root = get_settings().data_dir
        assert all((root / thumbs.thumb_file(Path(g.file_path), w)).is_file() for g in gens for w in thumbs.SIZES)
        for item in body["items"]:
            assert db.get(MediaItem, item["id"]).width == 1024
    for item in body["items"]:
        got = client.get(f"/api/media/{item['id']}").json()
        assert got["status"] == "ready" and client.get(got["media_url"]).status_code == 200


def test_a_big_model_keeps_one_job_per_picture(client):
    body = _make(client, count=8, model="qwen_image_2512", seed=10)
    assert len(body["items"]) == len(body["jobs"]) == 8
    with SessionLocal() as db:
        assert sorted(g.seed for g in _gens(db, body)) == list(range(10, 18))
        assert not any("batch" in (g.params or {}) for g in _gens(db, body))


def test_count_nine_is_refused(client):
    r = client.post("/api/images/generate", json={"prompt": "x", "count": 9})
    assert r.status_code == 422
    assert client.get("/api/images/options").json()["count"]["max"] == 8


def test_out_of_memory_queues_the_rest_one_by_one(client, checked):
    body = _make(client, count=4, seed=77)
    d = Recorder(fail=lambda p: bool(p.get("batch_out")))
    _run_all(lambda: d)
    # the batch, then the first alone, then the other three as their own jobs at their own places
    assert d.calls[0] == {"seed": 77, "index": 0, "size": 4}
    assert sorted((c["index"], c["size"]) for c in d.calls[1:]) == [(0, 1), (1, 1), (2, 1), (3, 1)]
    with SessionLocal() as db:
        gens = _gens(db, body)
        assert {g.status for g in gens} == {"ready"} and all(g.job_id for g in gens)


def test_a_refused_picture_fails_alone(client, monkeypatch):
    body = _make(client, count=3)
    with SessionLocal() as db:
        third = _gens(db, body)[2].id

    def check(ctx, gen, path, media_type):
        if gen.id == third:
            raise RuntimeError("That picture can't be made here")

    monkeypatch.setattr(content_policy, "check_after", check)
    _run_all(lambda: MockDriver(step_seconds=0))
    with SessionLocal() as db:
        gens = _gens(db, body)
        assert [g.status for g in gens] == ["ready", "ready", "failed"]
        job = db.get(Job, gens[2].job_id)
        assert job.status == "failed" and "can't be made" in job.error


def test_cancelling_the_batch_ends_its_pictures(client):
    body = _make(client, count=3)
    assert client.post(f"/api/jobs/{body['jobs'][0]['id']}/cancel").status_code == 200
    with SessionLocal() as db:
        gens = _gens(db, body)
        assert {g.status for g in gens} == {"failed"}
        assert {db.get(Job, g.job_id).status for g in gens} == {"cancelled"}


def test_deleting_the_first_picture_leaves_the_rest_queued(client, checked):
    body = _make(client, count=3)
    assert client.delete(f"/api/media/{body['items'][0]['id']}").status_code == 204
    with SessionLocal() as db:
        rest = [db.get(Generation, i["generation_id"]) for i in body["items"][1:]]
        assert all(db.get(Job, g.job_id).status == "queued" for g in rest)
    _run_all(lambda: MockDriver(step_seconds=0))
    with SessionLocal() as db:
        assert {db.get(Generation, i["generation_id"]).status for i in body["items"][1:]} == {"ready"}


def test_remake_of_picture_five_draws_it_again(client, checked):
    body = _make(client, count=8, seed=4321)
    _run_all(lambda: MockDriver(step_seconds=0))
    fifth = body["items"][4]
    with SessionLocal() as db:
        first = db.get(Generation, fifth["generation_id"])
        assert first.params["batch"]["index"] == 4
    d = Recorder()
    r = client.post(f"/api/media/{fifth['id']}/regenerate", json={"mode": "edit", "params": {"seed": 4321}})
    assert r.status_code == 202, r.text
    _run_all(lambda: d)
    assert d.calls == [{"seed": 4321, "index": 4, "size": 1}]
    with SessionLocal() as db:
        again = db.get(Generation, db.get(Job, r.json()["id"]).generation_id)
        assert again.params["batch"] == {"index": 4} and again.status == "ready"

    # a new seed is just a new picture
    d.calls.clear()
    client.post(f"/api/media/{fifth['id']}/regenerate", json={"mode": "same"})
    _run_all(lambda: d)
    assert d.calls[0]["index"] == 0

    # Reuse from the Create page: seed plus the place in the batch
    _make(client, count=1, seed=4321, batch_index=4)
    d.calls.clear()
    _run_all(lambda: d)
    assert d.calls == [{"seed": 4321, "index": 4, "size": 1}]
    assert client.post("/api/images/generate", json={"prompt": "x", "count": 2, "seed": 1,
                                                     "batch_index": 3}).status_code == 422


def test_mock_batch_pictures_differ_and_match_their_remake(tmp_path):
    d = MockDriver(step_seconds=0)
    outs = [tmp_path / f"b{i}.png" for i in range(3)]
    d.generate_image("x", {"kind": "image", "batch_out": [str(p) for p in outs[1:]]}, 5, outs[0], lambda *a: None)
    assert len({p.read_bytes() for p in outs}) == 3
    alone = tmp_path / "alone.png"
    d.generate_image("x", {"kind": "image", "batch_index": 2}, 5, alone, lambda *a: None)
    assert alone.read_bytes() == outs[2].read_bytes()


# ---------------------------------------------------------------- the ComfyUI side

class LookupStub:
    def generation_file(self, gid):
        raise AssertionError("no references in a batch")

    def project_aspect(self, _):
        return None


def test_plan_sets_the_batch_and_the_pick():
    p = plan_generation("image", "x", {"model": "zimage_turbo", "batch_out": ["a", None, "c"]}, 9, LookupStub())
    assert p.batch == (4, 0, 4)
    p = plan_generation("image", "x", {"model": "zimage_turbo", "batch_index": 5}, 9, LookupStub())
    assert p.batch == (6, 5, 1)
    assert plan_generation("image", "x", {"model": "zimage_turbo"}, 9, LookupStub()).batch is None


@pytest.mark.parametrize("template", ["zimage_t2i", "flux2_klein_t2i", "flux2_klein9b_t2i", "krea2_t2i",
                                      "ernie_turbo_t2i"])
def test_batch_latent_feeds_the_sampler_through_the_pick(template):
    from app.workflows import batch_latent

    graph, _ = build(template, {"prompt": "x", "width": 512, "height": 512, "seed": 1})
    batch_latent(graph, 6, 5, 1)
    lat = find_node(graph, "Latent")
    pick = find_node(graph, "Batch pick")
    assert graph[lat]["inputs"]["batch_size"] == 6
    assert graph[pick]["inputs"] == {"samples": [lat, 0], "batch_index": 5, "length": 1}
    assert graph[find_node(graph, "Sampler")]["inputs"]["latent_image"] == [pick, 0]


class BatchClient(ComfyClient):
    def __init__(self, n):
        self.graphs, self.n = [], n

        def server(request):
            name = request.url.params.get("filename", "")
            return httpx.Response(200, content=f"PNG {name}".encode())

        super().__init__("http://comfy.test", transport=httpx.MockTransport(server), websocket=False)

    async def run(self, graph, on_progress=None, timeout=0, weights=None):
        self.graphs.append(graph)
        save = find_node(graph, "Save")
        files = [{"filename": f"mixai_{i:05}_.png", "subfolder": "", "type": "output"} for i in range(self.n)]
        return "p1", {"outputs": {save: {"images": files}}, "status": {"messages": []}}


def test_driver_keeps_every_picture_in_order(tmp_path):
    c = BatchClient(3)

    async def factory():
        return c

    out = tmp_path / "lead.png"
    more = [tmp_path / "two.png", tmp_path / "three.png"]
    d = ComfyDriver(client_factory=factory, lookup=LookupStub())
    res = d._generate("x", {"kind": "image", "model": "zimage_turbo", "batch_out": [str(p) for p in more]}, 3,
                      out, lambda *a: None)
    assert [p.read_bytes() for p in (out, *more)] == [b"PNG mixai_00000_.png", b"PNG mixai_00001_.png",
                                                     b"PNG mixai_00002_.png"]
    rec = res.params_update["comfy"]
    assert (rec["inputs"]["batch_size"], rec["inputs"]["batch_index"], rec["seed"]) == (3, 0, 3)
    assert any(n["class_type"] == "LatentFromBatch" for n in c.graphs[0].values())

    short = BatchClient(2)

    async def short_factory():
        return short

    d = ComfyDriver(client_factory=short_factory, lookup=LookupStub())
    with pytest.raises(ComfyError, match="got 2"):
        d._generate("x", {"kind": "image", "model": "zimage_turbo", "batch_out": [str(p) for p in more]}, 3,
                    out, lambda *a: None)
