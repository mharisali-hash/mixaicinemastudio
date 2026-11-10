"""Milestone 6: uploads, Library, Image studio, templates, dashboard, media events (contract v5)."""
import io
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

from app import reel as rl
from app import uploads
from app.api.events import collect_changes
from app.config import get_settings
from app.db import SessionLocal
from app.drivers.comfy import plan_generation
from app.models import Generation, Job, MediaItem, utcnow
from app.workflows import build
from tests.test_storyboard import _approved, _run_all
from tests.test_upscale import film, small_segments  # noqa: F401  (fixtures)


def png_bytes(size=(320, 200), colour=(120, 80, 40), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, fmt)
    return buf.getvalue()


def upload(client, name, data, ctype="application/octet-stream", title=None):
    return client.post("/api/media/upload", files={"file": (name, data, ctype)},
                       data={"title": title} if title else None)


def media_files() -> list[Path]:
    root = get_settings().data_dir / "workspaces"
    return [p for p in root.rglob("*") if p.is_file() and "media" in p.parts] if root.exists() else []


# ------------------------------------------------------------------ uploads

def test_upload_image_creates_item_and_serves_file(client):
    r = upload(client, "Beach day.PNG", png_bytes(), "image/png")
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["kind"] == "image" and item["origin"] == "upload" and item["title"] == "Beach day"
    assert (item["width"], item["height"]) == (320, 200) and item["status"] == "ready"
    assert item["media_url"].startswith("/api/media/workspaces/")
    assert item["thumb_url"] == f"/api/media/thumb/{item['generation_id']}?w=512"
    # random stored name, not the user's
    assert "Beach" not in item["media_url"] and item["media_url"].endswith(".png")
    assert client.get(item["media_url"]).status_code == 200
    with SessionLocal() as db:
        g = db.get(Generation, item["generation_id"])
        assert (g.kind, g.status, g.target_type, g.target_id) == ("upload", "ready", "media", item["id"])
        assert g.params["original_name"] == "Beach day.PNG" and g.project_id is None
    assert not [p for p in media_files() if p.suffix == ".part"]

    r = upload(client, "x.jpg", png_bytes(fmt="JPEG"), title="My title")
    assert r.status_code == 201 and r.json()["title"] == "My title"


def test_upload_rejects_by_content(client):
    r = upload(client, "clip.mp4", png_bytes(), "video/mp4")  # a PNG wearing an .mp4 name
    assert r.status_code == 415 and "PNG" in r.json()["detail"]
    r = upload(client, "notes.txt", b"just some text, nothing to see" * 10, "text/plain")
    assert r.status_code == 415
    r = upload(client, "fake.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 200, "image/png")  # right magic, broken body
    assert r.status_code == 415
    r = upload(client, "pic.heic", b"\0\0\0\x18ftypheic" + b"\0" * 100)
    assert r.status_code == 415
    assert client.post("/api/media/upload", json={"file": "x"}).status_code == 415
    assert client.post("/api/media/upload", data={"title": "no file"}, files={"other": ("a", b"b")}).status_code == 422
    assert media_files() == []  # nothing left behind
    with SessionLocal() as db:
        assert db.query(MediaItem).count() == 0


def test_upload_size_limits_stream(client, monkeypatch):
    monkeypatch.setattr(uploads, "IMAGE_MAX", 50_000)
    big = png_bytes((800, 800))  # noise-free but still > 50 kB? make sure
    big += b"\0" * max(0, 60_000 - len(big))
    r = upload(client, "big.png", big, "image/png")
    assert r.status_code == 413 and "40 MB" in r.json()["detail"]
    assert media_files() == []

    # Starlette's spooling form parser must not be involved: the file streams straight into the media folder
    from starlette import formparsers

    def boom(*a, **k):
        raise AssertionError("request body was parsed by starlette")
    monkeypatch.setattr(formparsers.MultiPartParser, "parse", boom)
    chunks = []
    real_write = uploads._Sink.part_data

    def spy(self, data, start, end):
        chunks.append(end - start)
        return real_write(self, data, start, end)
    monkeypatch.setattr(uploads._Sink, "part_data", spy)
    r = upload(client, "ok.png", png_bytes((300, 300)), "image/png")
    assert r.status_code == 201, r.text
    assert chunks

    # a declared body over the video limit is refused before reading it
    r = client.post("/api/media/upload", content=b"x", headers={
        "content-type": "multipart/form-data; boundary=abc", "content-length": str(uploads.VIDEO_MAX + 2 * 1024**2)})
    assert r.status_code == 413


def test_sniff_table():
    assert uploads.sniff(png_bytes()[:64]) == "image/png"
    assert uploads.sniff(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert uploads.sniff(b"\0\0\0\x20ftypisom\0\0\0\0") == "video/mp4"
    assert uploads.sniff(b"\0\0\0\x14ftypqt  \0\0\0\0") == "video/quicktime"
    assert uploads.sniff(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01B\x82\x84webm") == "video/webm"
    assert uploads.sniff(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01B\x82\x88matroska") is None
    assert uploads.sniff(b"GIF89a") is None


def test_upload_video_probes_and_thumbs(client, film):
    r = upload(client, "film.mp4", film.read_bytes(), "video/mp4")
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["kind"] == "video" and (item["width"], item["height"]) == (320, 180)
    assert 5.9 <= item["duration_s"] <= 6.1
    assert item["thumb_url"] == f"/api/media/thumb/{item['generation_id']}?w=512"
    assert client.get(item["thumb_url"]).status_code == 200
    with SessionLocal() as db:
        g = db.get(Generation, item["generation_id"])
        assert g.media_type == "video/mp4" and g.params["fps"] == 24 and g.params["has_audio"] is True


# ------------------------------------------------------------------ library

def _make_render(project_id, title, status="ready", minutes_ago=0):
    with SessionLocal() as db:
        from app.models import Project

        p = db.get(Project, project_id)
        g = Generation(workspace_id=p.workspace_id, project_id=p.id, target_type="project", target_id=p.id,
                       kind="render", version=1, status=status, prompt="", seed=1, media_type="video/mp4",
                       params={"title": title, "duration_s": 6.0, "size": [320, 180]},
                       file_path=f"workspaces/{p.workspace_id}/projects/{p.id}/generations/x.mp4",
                       created_at=utcnow() - timedelta(minutes=minutes_ago))
        db.add(g)
        db.commit()
        return g.id


def test_library_filters_project_rows_and_pagination(client, project, character, fast_driver):
    a = upload(client, "alpha.png", png_bytes(), "image/png").json()
    b = upload(client, "beta.png", png_bytes(colour=(1, 2, 3)), "image/png").json()
    client.patch(f"/api/media/{b['id']}", json={"tags": ["Reef ", "reef", "Hero"]})
    old = _make_render(project["id"], "Full film", minutes_ago=30)
    new = _make_render(project["id"], "Full film", minutes_ago=5)
    _make_render(project["id"], "Scenes 1–2", status="queued")
    portrait = _approved(client, fast_driver, "character", character["id"], "portrait", "grey-haired diver")

    # standalone only by default, newest first
    items = client.get("/api/media").json()["items"]
    assert [i["id"] for i in items] == [b["id"], a["id"]]
    assert client.get("/api/media", params={"tag": "REEF"}).json()["items"][0]["id"] == b["id"]
    assert client.get(f"/api/media/{b['id']}").json()["tags"] == ["reef", "hero"]
    assert [i["id"] for i in client.get("/api/media", params={"q": "alph"}).json()["items"]] == [a["id"]]
    assert client.get("/api/media", params={"kind": "video"}).json()["items"] == []

    rows = client.get("/api/media", params={"include": "project"}).json()["items"]
    ids = [i["id"] for i in rows]
    assert new in ids and old not in ids  # newest render per title; unfinished ones are left out
    assert portrait["id"] in ids
    prow = next(i for i in rows if i["id"] == portrait["id"])
    assert prow["origin"] == "project" and prow["kind"] == "image" and prow["title"] == "Diver · Portrait"
    assert prow["project_title"] == "Reef" and prow["width"] == 768
    vrow = next(i for i in rows if i["id"] == new)
    assert vrow["kind"] == "video" and vrow["versions_count"] == 2 and vrow["title"] == "Full film"
    assert [i["id"] for i in client.get("/api/media", params={"origin": "project", "kind": "video"}).json()["items"]] == [new]
    assert client.get("/api/media", params={"origin": "project", "q": "diver"}).json()["items"][0]["id"] == portrait["id"]

    # walk all 4 rows two at a time
    seen, cursor = [], None
    while True:
        params = {"include": "project", "limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/media", params=params).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert seen == ids and len(seen) == 4

    detail = client.get(f"/api/media/{new}").json()
    assert detail["origin"] == "project" and [v["id"] for v in detail["versions"]] == [new, old]
    assert client.delete(f"/api/media/{new}").status_code == 409
    assert client.patch(f"/api/media/{portrait['id']}", json={"title": "x"}).status_code == 409
    assert client.get("/api/media/not-a-uuid").status_code == 404

    path = get_settings().data_dir / Path(a["media_url"].removeprefix("/api/media/"))
    assert path.is_file()
    assert client.delete(f"/api/media/{a['id']}").status_code == 204
    assert not path.is_file()
    assert client.get(f"/api/media/{a['id']}").status_code == 404


# ------------------------------------------------------------------ image studio

def test_generate_count_makes_items_and_jobs(client, fast_driver):
    r = client.post("/api/images/generate", json={"prompt": "a lighthouse at dusk", "aspect": "16:9", "count": 3,
                                                  "style": "cinematic", "seed": 7, "steps": 9})
    assert r.status_code == 202, r.text
    body = r.json()
    # the default model draws the three in one batch: three items, one job (more in test_image_batch.py)
    assert len(body["items"]) == 3 and len(body["jobs"]) == 1
    assert {i["status"] for i in body["items"]} == {"queued"} and body["items"][0]["media_url"] is None
    with SessionLocal() as db:
        gens = [db.get(Generation, i["generation_id"]) for i in body["items"]]
        assert [g.seed for g in gens] == [7, 7, 7]
        g = gens[0]
        assert g.kind == "image" and g.target_type == "media" and "anamorphic" in g.prompt
        assert (g.params["width"], g.params["height"]) == (1344, 768) and g.params["steps"] == 9
    assert client.post("/api/images/generate", json={"prompt": "x", "style": "vaporwave"}).status_code == 422
    assert client.post("/api/images/generate", json={"prompt": "x", "count": 9}).status_code == 422

    _run_all(fast_driver)
    for it in body["items"]:
        item = client.get(f"/api/media/{it['id']}").json()
        assert item["status"] == "ready" and item["media_url"].startswith("/api/media/workspaces/")
        assert "/media/" in item["media_url"] and (item["width"], item["height"]) == (1344, 768)
        assert client.get(item["media_url"]).status_code == 200


def test_aspect_sizes_are_about_one_megapixel():
    from app.library import ASPECTS, size_for_aspect

    for a in ASPECTS:
        w, h = size_for_aspect(a)
        assert w % 64 == 0 and h % 64 == 0 and 0.85e6 <= w * h <= 1.2e6, a


def test_edit_with_sources(client, fast_driver, character):
    up_ = upload(client, "face.png", png_bytes((512, 768)), "image/png").json()
    portrait = _approved(client, fast_driver, "character", character["id"], "portrait")
    r = client.post("/api/images/edit", json={"source_ids": [up_["id"]], "instruction": "make it night"})
    assert r.status_code == 202, r.text
    one = r.json()["items"][0]
    with SessionLocal() as db:
        g = db.get(Generation, one["generation_id"])
        assert g.params["reference_ids"] == [up_["generation_id"]]
        assert g.params["edit_of"] == [{"media_id": up_["id"], "generation_id": up_["generation_id"]}]
        assert (g.params["width"], g.params["height"]) == (832, 1280)  # kept the 2:3 source shape
    r = client.post("/api/images/edit", json={"source_ids": [up_["id"], portrait["id"], up_["generation_id"]],
                                              "instruction": "put both on a boat", "count": 2, "aspect": "1:1"})
    assert r.status_code == 202 and len(r.json()["items"]) == 2
    assert client.post("/api/images/edit", json={"source_ids": [up_["id"]] * 4, "instruction": "x"}).status_code == 422
    assert client.post("/api/images/edit", json={"source_ids": [], "instruction": "x"}).status_code == 422
    assert client.post("/api/images/edit", json={"source_ids": ["nope"], "instruction": "x"}).status_code == 404
    _run_all(fast_driver)
    assert client.get(f"/api/media/{one['id']}").json()["status"] == "ready"


def test_comfy_plans_for_media_images(tmp_path):
    src = tmp_path / "a.png"

    class Lookup:
        def generation_file(self, gid):
            return src

    plan = plan_generation("image", "a fox", {"width": 1344, "height": 768, "steps": 8}, 3, Lookup())
    assert plan.template == "zimage_t2i" and (plan.inputs["width"], plan.inputs["height"]) == (1344, 768)
    build(plan.template, plan.inputs)
    plan = plan_generation("image", "make it snow", {"width": 1024, "height": 1024, "reference_ids": ["g1", "g2"]},
                           3, Lookup())
    assert plan.template == "qwen_edit" and plan.images == {"images": [src, src]}
    plan = plan_generation("image", "", {"upscale": {"template": "image_upscale_esrgan", "width": 2048,
                                                     "height": 2048, "source_id": "g1"}}, 3, Lookup())
    assert plan.template == "image_upscale_esrgan"


def test_regenerate_modes(client, fast_driver):
    item = client.post("/api/images/generate", json={"prompt": "a red kite over hills", "style": "photoreal"}).json()["items"][0]
    _run_all(fast_driver)
    first_gen = item["generation_id"]

    r = client.post(f"/api/media/{item['id']}/regenerate", json={"mode": "same"})
    assert r.status_code == 202, r.text
    # the current version stays the finished one until the new one is done
    assert client.get(f"/api/media/{item['id']}").json()["generation_id"] == first_gen
    _run_all(fast_driver)
    d = client.get(f"/api/media/{item['id']}").json()
    assert d["versions_count"] == 2 and d["generation_id"] != first_gen
    assert [v["version"] for v in d["versions"]] == [2, 1]

    # no LLM in tests: the note is appended and the job says so
    r = client.post(f"/api/media/{item['id']}/regenerate", json={"mode": "note", "note": "make the kite blue"})
    assert r.status_code == 202 and "note appended" in r.json()["message"]
    assert client.post(f"/api/media/{item['id']}/regenerate", json={"mode": "note"}).status_code == 422
    r = client.post(f"/api/media/{item['id']}/regenerate", json={"mode": "edit", "prompt": "a yellow kite",
                                                                 "params": {"seed": 42}})
    assert r.status_code == 202
    with SessionLocal() as db:
        g = db.get(Generation, db.get(Job, r.json()["id"]).generation_id)
        assert g.prompt == "a yellow kite" and g.seed == 42 and g.version == 4
    _run_all(fast_driver)
    assert client.get(f"/api/media/{item['id']}").json()["versions_count"] == 4

    up_ = upload(client, "u.png", png_bytes(), "image/png").json()
    assert client.post(f"/api/media/{up_['id']}/regenerate", json={"mode": "same"}).status_code == 422


# ------------------------------------------------------------------ upscale on uploads

def test_upscale_uploaded_image(client, fast_driver):
    item = upload(client, "small.webp", png_bytes((400, 300), fmt="WEBP"), "image/webp").json()
    r = client.post(f"/api/generations/{item['generation_id']}/upscale", json={"engine": "quick", "target": "2x"})
    assert r.status_code == 202, r.text
    opts = client.get("/api/system/upscale-options", params={"generation_id": item["generation_id"]}).json()
    assert opts["media"] == "image" and opts["source"]["width"] == 400
    _run_all(fast_driver)
    d = client.get(f"/api/media/{item['id']}").json()
    assert d["versions_count"] == 2 and (d["width"], d["height"]) == (800, 600)
    newest = d["versions"][0]
    assert newest["kind"] == "image" and newest["version"] == 2 and newest["parent_id"] == item["generation_id"]
    assert d["generation_id"] == newest["id"] and d["origin"] == "upload"
    # and a redraw of an upload still has a prompt to steer Z-Image
    r = client.post(f"/api/generations/{item['generation_id']}/upscale", json={"engine": "redraw", "target": "2x"})
    with SessionLocal() as db:
        g = db.get(Generation, db.get(Job, r.json()["id"]).generation_id)
        assert g.params["upscale"]["prompt"]


def test_upscale_generated_image(client, fast_driver):
    item = client.post("/api/images/generate", json={"prompt": "a quiet harbour", "aspect": "1:1"}).json()["items"][0]
    _run_all(fast_driver)
    gid = client.get(f"/api/media/{item['id']}").json()["generation_id"]
    assert client.post(f"/api/generations/{gid}/upscale", json={"engine": "quick", "target": "2x"}).status_code == 202
    _run_all(fast_driver)
    d = client.get(f"/api/media/{item['id']}").json()
    assert (d["width"], d["height"]) == (2048, 2048) and d["versions_count"] == 2
    # regenerating after an upscale makes a fresh 1 MP picture, not another upscale
    client.post(f"/api/media/{item['id']}/regenerate", json={"mode": "same"})
    _run_all(fast_driver)
    d = client.get(f"/api/media/{item['id']}").json()
    assert (d["width"], d["height"]) == (1024, 1024) and d["versions_count"] == 3


def test_upscale_uploaded_video(client, film, small_segments):
    from app import worker
    from app.drivers.mock import MockDriver

    item = upload(client, "film.mp4", film.read_bytes(), "video/mp4").json()
    r = client.post(f"/api/generations/{item['generation_id']}/upscale", json={"engine": "quick", "target": "1080p"})
    assert r.status_code == 202, r.text
    assert client.post(f"/api/generations/{item['generation_id']}/upscale",
                       json={"engine": "redraw"}).status_code == 422
    while worker.process_one(driver_factory=lambda: MockDriver(step_seconds=0, steps=1)):
        pass
    with SessionLocal() as db:
        job = db.get(Job, r.json()["id"])
        assert job.status == "done", job.error
    d = client.get(f"/api/media/{item['id']}").json()
    assert (d["width"], d["height"]) == (1920, 1080) and d["versions_count"] == 2 and d["kind"] == "video"
    newest = d["versions"][0]
    assert newest["kind"] == "video" and newest["target_type"] == "media" and "/media/" in newest["media_url"]
    assert newest["params"]["title"] == "film · 1080p"
    out = get_settings().data_dir / newest["media_url"].removeprefix("/api/media/")
    assert rl.probe(out).has_audio
    assert d["thumb_url"]  # the upload's poster still stands in


def test_upscale_uploaded_webm_is_normalised(client, tmp_path, small_segments):
    from app import worker
    from app.drivers.mock import MockDriver

    clip = tmp_path / "c.webm"
    try:
        rl.run_ff(["-f", "lavfi", "-i", "testsrc2=size=256x144:rate=25:duration=1.2",
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-c:v", "libvpx", "-b:v", "300k",
                   "-c:a", "libvorbis", str(clip)])
    except rl.FFmpegError:
        pytest.skip("this ffmpeg build has no libvpx/libvorbis")
    item = upload(client, "c.webm", clip.read_bytes(), "video/webm").json()
    assert item["kind"] == "video" and item["media_type"] == "video/webm"
    r = client.post(f"/api/generations/{item['generation_id']}/upscale", json={"engine": "quick", "target": "1080p"})
    assert r.status_code == 202, r.text
    while worker.process_one(driver_factory=lambda: MockDriver(step_seconds=0, steps=1)):
        pass
    with SessionLocal() as db:
        assert db.get(Job, r.json()["id"]).status == "done", db.get(Job, r.json()["id"]).error
    d = client.get(f"/api/media/{item['id']}").json()
    out = get_settings().data_dir / d["media_url"].removeprefix("/api/media/")
    p = rl.probe(out)
    assert (p.width, p.height) == (1920, 1080) and p.has_audio and out.suffix == ".mp4"


# ------------------------------------------------------------------ templates, dashboard, events

def test_templates(client):
    vids = client.get("/api/templates", params={"type": "video"}).json()
    imgs = client.get("/api/templates", params={"type": "image"}).json()
    assert len(vids) >= 16 and len(imgs) >= 24
    sounds = client.get("/api/templates", params={"type": "audio"}).json()
    assert len(client.get("/api/templates").json()) == len(vids) + len(imgs) + len(sounds)

    r = client.post("/api/templates/video-product-ad-30s/start").json()
    assert r["target"] == "quick" and r["prefill"]["duration_s"] == 30 and "[product]" in r["prefill"]["prompt"]
    assert "product" in r["prefill"]["placeholders"]
    r = client.post("/api/templates/video-short-film-5min/start").json()
    assert r["target"] == "studio" and r["prefill"]["target_runtime_s"] == 300
    r = client.post("/api/templates/image-poster/start").json()
    assert r["target"] == "image" and r["prefill"]["aspect"] == "2:3" and r["prefill"]["style"] == "cinematic"
    assert client.post("/api/templates/nope/start").status_code == 404

    # every template's defaults must pass the forms they prefill
    from app.api.quick import QuickIn
    from app.library import ASPECTS, STYLES

    for t in vids:
        d = t["defaults"]
        if "authoring_mode" not in d:
            QuickIn(prompt=d["prompt_scaffold"], duration_s=d["duration_s"], aspect_ratio=d["aspect_ratio"],
                    style=d["style"], dialogue=d["dialogue"])
    for t in imgs:
        d = t["defaults"]
        if t.get("target", "image-generate") == "image-generate":
            assert d["aspect"] in ASPECTS and d["style"] in STYLES
        else:
            # Edit Image has no style picker, and most edits keep the photo's own shape
            assert d.get("aspect") is None or d["aspect"] in ASPECTS
    assert client.get("/api/images/options").json()["styles"]


def test_dashboard(client, project, fast_driver):
    client.post("/api/images/generate", json={"prompt": "a fern", "count": 2})
    upload(client, "a.png", png_bytes(), "image/png")
    _make_render(project["id"], "Full film")
    d = client.get("/api/dashboard").json()
    assert [p["id"] for p in d["recent_projects"]] == [project["id"]]
    assert len(d["recent_images"]) == 3 and len(d["recent_videos"]) == 1
    # both ferns come from one batch job
    assert len(d["running_jobs"]) == 1 and d["quick_recent"] == []
    _run_all(fast_driver)
    assert client.get("/api/dashboard").json()["running_jobs"] == []


def test_media_events(client, fast_driver):
    since = utcnow() - timedelta(seconds=1)
    item = client.post("/api/images/generate", json={"prompt": "a kettle"}).json()["items"][0]
    me = client.get("/api/auth/me").json()
    rows = [r for r in collect_changes(me["workspace_id"], since) if r[0] == "media"]
    assert rows and rows[0][1] == item["id"] and rows[0][3]["status"] == "queued"

    mark = utcnow()
    _run_all(fast_driver)
    rows = [r for r in collect_changes(me["workspace_id"], mark) if r[0] == "media"]
    assert rows and rows[-1][3]["status"] == "ready" and rows[-1][3]["media_url"]


def test_old_file_urls_still_work(client, fast_driver, character):
    portrait = _approved(client, fast_driver, "character", character["id"], "portrait")
    assert portrait["media_url"].startswith("/api/media/workspaces/")
    assert client.get(portrait["media_url"]).status_code == 200
    # and the JSON route answers for the same generation as a Library row
    assert client.get(f"/api/media/{portrait['id']}").json()["origin"] == "project"
