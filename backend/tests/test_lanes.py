"""Two GPUs: image / video / general lanes, lane workers and lane-aware recovery."""
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import autopilot as ap_mod
from app import worker
from app.config import Settings
from app.db import SessionLocal
from app.drivers.base import DriverResult
from app.drivers.comfy import ComfyDriver
from app.lanes import dub_lane, lane_for, parse_lanes
from app.models import Job, WorkerHeartbeat, utcnow
from app.services import enqueue_generation
from app.worker import claim_next, process_one, recover_lanes

from tests.test_autopilot import brain, create, stages  # noqa: F401  (brain is a fixture)


# ------------------------------------------------------------------ classifier

@pytest.mark.parametrize("kind,lane", [
    ("portrait", "image"), ("sheet_view", "image"), ("establishing", "image"), ("keyframe_start", "image"),
    ("keyframe_end", "image"), ("keyframe_mid", "image"), ("image", "image"), ("model3d", "image"),
    ("take", "video"), ("video", "video"), ("render", "video"), ("tile", "video"),
    ("song", "audio"), ("music", "audio"), ("sfx", "audio"), ("speech", "audio"),
    ("scene_text", "general"), (None, "general"),
])
def test_generate_jobs_follow_their_generation_kind(kind, lane):
    assert lane_for("generate", kind) == lane


def test_every_registered_job_type_has_a_lane():
    # a new job type has to be put somewhere on purpose, not fall into "general" by accident
    expected = {
        "generate": None,  # by kind, above
        "upscale": "video",
        "autopilot": "general", "reel_assemble": "general", "brand_apply": "general", "brand_reveal": "general",
        "media_zip": "general", "ai_beats": "general",
        "photo_render": "general", "look_video": "general",  # numpy / ffmpeg on the CPU
        "audio_render": "general", "audio_mix": "general",
        "voice_transcribe": "audio",  # faster-whisper on whichever GPU worker claims it
        "voice_sample": "audio",  # a gallery voice's sample line through a speech engine
        "audio_stems": "audio",  # audio-separator (RoFormer / Demucs) on whichever GPU worker claims it
        "script_mix": "general", "script_stems": "general",  # dialogue: ffmpeg joins of finished takes
        "transcribe": "audio", "transcript_cleanup": "general", "transcript_video": "general",
        "dub_stage": None,  # by payload.stage, below
        "dub_export": "general",
        # podcast: LLM writing, ffmpeg mixes / encodes of finished takes
        "podcast_write": "general", "podcast_mix": "general", "podcast_publish": "general", "podcast_video": "general",
        # AI video podcast: the orchestrator (its takes are generate jobs on the video lane) and the ffmpeg cut
        "video_podcast": "general", "video_podcast_assemble": "general",
        # character sheets: the board is Pillow; its panels are generate jobs on the image lane
        "character_board": "general",
        # story books: the narration speaks and checks on a GPU; the video is ffmpeg
        "story_audio": "audio", "story_video": "general",
        # Poetry Shorts: the orchestrator, the vocal split + Whisper timing on a GPU, the ffmpeg cut
        "poetry_short": "general", "poetry_timing": "audio", "poetry_render": "general",
        # Ad Studio: the orchestrator writes concepts and lays out with Pillow; its photos are generate jobs
        "ad_campaign": "general",
        "ad_style": "general",  # the vision model reads the brand's own adverts
        # long Urdu/Arabic prose marked in the background (contract v23b): LLM calls only
        "text_aerab": "general",
        # 3D Studio's logo badge: traced and extruded with numpy; its cut-out is a generate job on the image lane
        "model3d_logo": "general",
        # Presenter Video: the orchestrator (its takes are generate jobs) and the ffmpeg cut
        "presenter_video": "general", "presenter_assemble": "general",
        # admin bulk runs: the orchestrator, the ZIP unpack and the export; each picture is a generate job
        "bulk_run": "general", "bulk_unzip": "general", "bulk_export": "general",
    }
    for t in worker.HANDLERS:
        if t.startswith("ai_"):
            assert lane_for(t) == "general", t
            continue
        assert t in expected, f"classify new job type {t!r} in app.lanes"
        if expected[t]:
            assert lane_for(t) == expected[t], t


def test_dub_stages_split_between_the_gpu_and_the_cpu():
    from app.dub.project import STAGES

    gpu = {s for s in STAGES if dub_lane(s) == "audio"}
    assert gpu == {"separate", "transcribe", "render", "verify"}
    assert all(dub_lane(s) == "general" for s in STAGES if s not in gpu)


def test_parse_lanes():
    assert parse_lanes(None) == ("image", "video", "audio", "general")
    assert parse_lanes("all") == ("image", "video", "audio", "general")
    assert parse_lanes("video") == ("video",)
    assert parse_lanes("image,general") == ("image", "general")
    assert parse_lanes("image-video") == ("image", "video")  # systemd instance names have no commas
    assert parse_lanes("video-audio") == ("video", "audio")
    with pytest.raises(ValueError):
        parse_lanes("gpu2")


def _gen_job(db, project_id, kind, target_id="t1"):
    g = enqueue_generation(db, workspace_id=_ws(db), project_id=project_id, target_type="character",
                           target_id=target_id, kind=kind, prompt="p", params={})
    db.commit()
    return db.get(Job, g.job_id)


def _ws(db):
    from app.models import Workspace

    return db.scalars(select(Workspace.id)).first()


def test_lane_is_stamped_at_enqueue(client, character, db):
    r = client.post("/api/generations", json={"target_type": "character", "target_id": character["id"],
                                              "kind": "portrait", "prompt": "p"})
    job = db.get(Job, r.json()["job_id"])
    assert job.lane == "image"
    take = _gen_job(db, character["project_id"], "take", character["id"])
    assert take.lane == "video"
    other = Job(workspace_id=_ws(db), type="media_zip", payload={})
    db.add(other)
    db.commit()
    assert other.lane == "general"
    rows = {j["id"]: j["lane"] for j in client.get("/api/jobs").json()}
    assert rows[job.id] == "image" and rows[take.id] == "video"


# ------------------------------------------------------------------ ComfyUI URL per lane

def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_lane_urls_and_fallback():
    split = _settings(comfy_urls="http://a:1", comfy_image_urls="http://127.0.0.1:8188",
                      comfy_video_urls="http://127.0.0.1:8189/")
    assert split.comfy_urls_for("image") == ["http://127.0.0.1:8188"]
    assert split.comfy_urls_for("video") == ["http://127.0.0.1:8189"]
    assert split.comfy_urls_for("general") == ["http://127.0.0.1:8188"]  # borrows the image GPU
    assert ComfyDriver(settings=split, lane="video").urls == ["http://127.0.0.1:8189"]
    assert ComfyDriver(settings=split, lane="image").urls == ["http://127.0.0.1:8188"]

    legacy = _settings(comfy_urls="http://127.0.0.1:8189,http://127.0.0.1:8188")
    for lane in ("image", "video", "general", None):
        assert legacy.comfy_urls_for(lane) == ["http://127.0.0.1:8189", "http://127.0.0.1:8188"]

    only_video = _settings(comfy_video_urls="http://v:1")
    assert only_video.comfy_urls_for("video") == ["http://v:1"]
    assert only_video.comfy_urls_for("image") == ["http://v:1"]  # last resort: anything configured
    assert only_video.all_comfy_urls() == ["http://v:1"]


def test_worker_builds_the_driver_for_the_jobs_lane(client, character, db, monkeypatch):
    from app.drivers.mock import MockDriver

    seen = []

    def fake_get_driver(name, lane=None):
        seen.append(lane)
        return MockDriver(step_seconds=0)

    monkeypatch.setattr(worker, "get_driver", fake_get_driver)
    _gen_job(db, character["project_id"], "portrait", character["id"])
    assert process_one(lanes=("image",))
    assert seen == ["image"]


# ------------------------------------------------------------------ claiming

def test_lane_worker_claims_only_its_lane(client, character, db):
    img = _gen_job(db, character["project_id"], "portrait", character["id"])
    vid = _gen_job(db, character["project_id"], "take", character["id"])
    gen = Job(workspace_id=_ws(db), type="media_zip", payload={})
    db.add(gen)
    db.commit()

    with SessionLocal() as a:
        assert claim_next(a, lanes=("video",)).id == vid.id
        assert claim_next(a, lanes=("video",)) is None
    with SessionLocal() as a:
        assert claim_next(a, lanes=("general",)).id == gen.id
        assert claim_next(a, lanes=("image",)).id == img.id
        assert claim_next(a, lanes=("image", "video", "general")) is None


def test_default_worker_still_takes_everything(client, character, db):
    _gen_job(db, character["project_id"], "portrait", character["id"])
    _gen_job(db, character["project_id"], "take", character["id"])
    with SessionLocal() as a:
        assert claim_next(a) is not None and claim_next(a) is not None


class _Overlap:
    """Fake GPU that only finishes once both lanes are inside a render at the same time."""

    def __init__(self):
        self.barrier = threading.Barrier(2, timeout=10)
        self.spans = {}

    def driver(self):
        outer = self

        class D:
            name = "mock"

            def _run(self, kind, out_path, cb, media):
                t0 = time.monotonic()
                outer.barrier.wait()  # raises BrokenBarrierError if the other lane isn't rendering too
                for i in range(3):
                    time.sleep(0.05)
                    cb((i + 1) / 4, "Rendering")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"x")
                outer.spans[kind] = (t0, time.monotonic())
                return DriverResult(out_path, media, {})

            def generate_image(self, prompt, params, seed, out_path, cb):
                return self._run("image", out_path, cb, "image/png")

            def generate_video(self, prompt, params, seed, out_path, cb):
                return self._run("video", out_path, cb, "video/mp4")

        return D()


def test_image_and_video_lanes_run_at_the_same_time(client, character, db):
    img = _gen_job(db, character["project_id"], "portrait", character["id"])
    vid = _gen_job(db, character["project_id"], "take", character["id"])
    gpu = _Overlap()
    errors = []

    def lane_worker(lanes):
        try:
            assert process_one(driver_factory=gpu.driver, lanes=lanes)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=lane_worker, args=(("image",),)),
               threading.Thread(target=lane_worker, args=(("video",),))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    with SessionLocal() as s:
        assert s.get(Job, img.id).status == "done", s.get(Job, img.id).error
        assert s.get(Job, vid.id).status == "done", s.get(Job, vid.id).error
    (a0, a1), (b0, b1) = gpu.spans["image"], gpu.spans["video"]
    assert a0 < b1 and b0 < a1  # the two renders overlapped


# ------------------------------------------------------------------ recovery

def _beat(db, wid, lanes, age=timedelta(0)):
    db.add(WorkerHeartbeat(id=wid, last_seen=utcnow() - age, info={"lanes": list(lanes)}))
    db.commit()


def _run(db, job):
    job.status, job.attempts, job.heartbeat_at = "running", 1, utcnow()
    db.commit()


def test_recovery_leaves_another_live_lanes_job_alone(client, character, db):
    img = _gen_job(db, character["project_id"], "portrait", character["id"])
    vid = _gen_job(db, character["project_id"], "take", character["id"])
    _run(db, img)
    _run(db, vid)
    _beat(db, "gpu-box:111", ["image"])  # the image worker is alive and rendering

    # the video worker restarts: its own lane's running job is an orphan, the image one is not its business
    assert recover_lanes(db, ("video",)) == 1
    db.refresh(img)
    db.refresh(vid)
    assert vid.status == "queued" and img.status == "running"

    # a second image worker coming up must not steal the first one's live job either...
    assert recover_lanes(db, ("image",)) == 0
    # ...until it goes quiet
    img.heartbeat_at = utcnow() - timedelta(minutes=5)
    db.commit()
    assert recover_lanes(db, ("image",)) == 1
    db.refresh(img)
    assert img.status == "queued"


def test_old_all_lanes_heartbeat_counts_for_every_lane(client, character, db):
    vid = _gen_job(db, character["project_id"], "take", character["id"])
    _run(db, vid)
    db.add(WorkerHeartbeat(id="gpu-box:222", last_seen=utcnow(), info={"driver": "comfy"}))
    db.commit()
    assert recover_lanes(db, ("video",)) == 0
    # a dead (stale) heartbeat doesn't protect anything
    db.get(WorkerHeartbeat, "gpu-box:222").last_seen = utcnow() - timedelta(minutes=10)
    db.commit()
    assert recover_lanes(db, ("video",)) == 1


def test_status_reports_each_lane(client, character, db):
    img = _gen_job(db, character["project_id"], "portrait", character["id"])
    _run(db, img)
    _beat(db, "gpu-box:1", ["image"])
    _beat(db, "gpu-box:2", ["video"], age=timedelta(minutes=5))
    lanes = client.get("/api/system/status").json()["lanes"]
    assert set(lanes) == {"image", "video", "audio", "general"}
    assert lanes["image"]["worker"]["alive"] is True and lanes["image"]["job"]["id"] == img.id
    assert lanes["video"]["worker"]["alive"] is False and lanes["video"]["job"] is None
    assert lanes["general"]["comfy"] is None
    assert lanes["image"]["comfy"]["ok"] is False and lanes["image"]["comfy"]["urls"] == []


# ------------------------------------------------------------------ autopilot across lanes

def test_autopilot_completes_with_children_on_other_lanes(client, brain, fast_driver, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ap_mod, "POLL_S", 0.0)  # the general worker would otherwise poll 10 s per wait
    brain()
    out = create(client)
    parent_id = out["job"]["id"]
    claimed = {"image": [], "video": [], "general": []}
    real_claim = worker.claim_next

    def spy(db, gpu=None, lanes=None):
        job = real_claim(db, gpu=gpu, lanes=lanes)
        if job is not None:
            claimed[lanes[0]].append((job.type, job.lane))
        return job

    monkeypatch.setattr(worker, "claim_next", spy)
    for _ in range(200):
        with SessionLocal() as s:
            if s.get(Job, parent_id).status in ("done", "failed", "cancelled"):
                break
        busy = [process_one(driver_factory=fast_driver, lanes=(lane,)) for lane in ("general", "image", "video")]
        if not any(busy):
            break

    with SessionLocal() as s:
        parent = s.get(Job, parent_id)
        assert parent.status == "done", parent.error
        assert parent.lane == "general"
        assert stages(parent)["render"] == "done" and stages(parent)["stitch"] == "done"
        kids = s.scalars(select(Job).where(Job.id != parent_id, Job.type == "generate")).all()
        assert {j.lane for j in kids} == {"image", "video"}
        assert all(j.status == "done" for j in kids)
    # nobody ran a job outside its lane
    for lane, rows in claimed.items():
        assert rows and all(row_lane == lane for _t, row_lane in rows), (lane, rows)
    assert any(t == "generate" for t, _ in claimed["video"]) and any(t == "reel_assemble" for t, _ in claimed["general"])

