"""Presenter Video (8 Nov): a character presenting a script; one edited scene is the only one made again."""
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Generation, MediaItem
from app.presenter import service as svc
from tests.test_storyboard import _run_all

SCRIPT = ("Welcome to our little kitchen. Today we bake a simple loaf of bread together.\n\n"
          "First we warm the water and wake the yeast. Then we mix in the flour.\n\n"
          "Knead it for ten minutes, cover it, and let it rest until it doubles.")


def _presenter_character(client, fast_driver) -> dict:
    c = client.post("/api/characters", json={"name": "Ayesha", "description": "a cheerful baker"}).json()
    assert client.post("/api/generations", json={"target_type": "character", "target_id": c["id"], "kind": "portrait",
                                                 "prompt": "portrait"}).status_code == 201
    v = client.post("/api/voices", json={"kind": "design", "name": "Ayesha's voice", "language": "en",
                                         "design": {"description": "a warm, friendly woman's voice"}}).json()
    assert client.patch(f"/api/characters/{c['id']}", json={"voice_id": v["id"]}).status_code == 200
    _run_all(fast_driver)
    return c


def _scene_gens(kind: str) -> dict[str, int]:
    with SessionLocal() as db:
        rows = db.scalars(select(Generation).where(Generation.target_type == svc.TARGET, Generation.kind == kind)).all()
        out: dict[str, int] = {}
        for g in rows:
            out[g.target_id] = out.get(g.target_id, 0) + 1
        return out


def test_split_script():
    parts = svc.split_script(SCRIPT)
    assert len(parts) == 3 and parts[0].startswith("Welcome")
    long = " ".join(f"This is sentence number {i} of a long paragraph." for i in range(12))
    assert all(len(p) <= svc.SCENE_CHARS for p in svc.split_script(long))
    assert svc.split_script("   ") == []


def test_long_sentences_are_cut_at_commas_then_words():
    # two long Russian sentences used to share one scene and run 18 s with a slow voice
    ru = ("Каждый вечер, когда солнце садится за горы, старый пастух выходит на крыльцо и смотрит на небо. "
          "Он ждёт, когда загорится первая звезда, потому что верит, что в этот час ангел слышит каждое слово.")
    parts = svc.split_script(ru)
    assert len(parts) == 2 and parts[1].startswith("Он")
    one = ru.replace(". Он", ", и он")
    parts = svc.split_script(one)
    assert len(parts) >= 2 and all(len(p) <= svc.SCENE_CHARS for p in parts)
    assert " ".join(parts) == " ".join(one.split()) and parts[0].endswith(",")
    run_on = " ".join(["слово"] * 60)
    assert all(len(p) <= svc.SCENE_CHARS for p in svc.split_text(run_on, svc.SCENE_CHARS))


def test_needs_a_voice_and_a_portrait(client, fast_driver):
    c = client.post("/api/characters", json={"name": "Bilal"}).json()
    r = client.post("/api/presenter", json={"character_id": c["id"], "script": "Hello."})
    assert r.status_code == 422 and "voice" in r.json()["detail"]


def test_end_to_end_and_only_the_edited_scene_is_redone(client, fast_driver):
    c = _presenter_character(client, fast_driver)
    r = client.post("/api/presenter", json={"character_id": c["id"], "title": "Bread at home", "script": SCRIPT,
                                            "settings": {"aspect": "9:16", "caption_style": "classic"}})
    assert r.status_code == 201, r.text
    pv = r.json()
    assert len(pv["scenes"]) == 3 and pv["settings"]["lower_name"] == "Ayesha"
    _run_all(fast_driver)
    pv = client.get(f"/api/presenter/{pv['id']}").json()
    stages = {s["key"]: s["status"] for s in pv["stages"]}
    # voices and frames are made, then it stops for a look at the frames
    assert stages["speech"] == "done" and stages["frames"] == "review", pv["stages"]
    assert all(s["speech"]["status"] == "ready" and s["frame"]["status"] == "ready" for s in pv["scenes"])
    assert all(s["duration_s"] for s in pv["scenes"])

    r = client.post(f"/api/presenter/{pv['id']}/render", json={"quality": "draft"})
    assert r.status_code == 202, r.text
    _run_all(fast_driver)
    pv = client.get(f"/api/presenter/{pv['id']}").json()
    assert {s["key"]: s["status"] for s in pv["stages"]}["assemble"] == "done", pv["stages"]
    out = pv["outputs"]["draft"]
    assert out["width"] == 720 and out["height"] == 1280 and out["duration_s"] > 0
    with SessionLocal() as db:
        item = db.get(MediaItem, out["media_id"])
        assert item.kind == "video" and db.get(Generation, item.generation_id).status == "ready"

    before = {k: _scene_gens(k) for k in ("speech", "image", "video")}
    scenes = [{"id": s["id"], "text": s["text"], "background": s["background"]} for s in pv["scenes"]]
    scenes[1]["text"] = "First we warm the water a little and wake the yeast with a pinch of sugar."
    r = client.patch(f"/api/presenter/{pv['id']}", json={"scenes": scenes})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/presenter/{pv['id']}/render", json={"quality": "draft"}).status_code == 202
    _run_all(fast_driver)
    after = {k: _scene_gens(k) for k in ("speech", "image", "video")}
    second = pv["scenes"][1]["id"]
    # the edited scene has a new voice and a new take; its frame (same look, same background) stays
    assert after["speech"][second] == before["speech"][second] + 1
    assert after["video"][second] == before["video"][second] + 1
    assert after["image"] == before["image"]
    for other in (pv["scenes"][0]["id"], pv["scenes"][2]["id"]):
        assert after["speech"][other] == before["speech"][other] and after["video"][other] == before["video"][other]
    pv2 = client.get(f"/api/presenter/{pv['id']}").json()
    assert pv2["outputs"]["draft"]["media_id"] == out["media_id"]  # a new version of the same Library video


def test_a_script_with_banned_words_is_refused(client, fast_driver, monkeypatch):
    c = _presenter_character(client, fast_driver)
    from app import content_policy

    monkeypatch.setattr(content_policy, "quick_refusal", lambda text, admin: "Not allowed" if "forbidden" in text else None)
    r = client.post("/api/presenter", json={"character_id": c["id"], "script": "This is forbidden talk."})
    assert r.status_code == 422 and r.json()["detail"] == "Not allowed"


def test_a_scene_too_long_to_film_says_so(client, fast_driver, monkeypatch):
    c = _presenter_character(client, fast_driver)
    monkeypatch.setattr(svc, "MAX_SCENE_S", 0.1)
    r = client.post("/api/presenter", json={"character_id": c["id"], "script": "A sentence long enough to count."})
    _run_all(fast_driver)
    pv = client.get(f"/api/presenter/{r.json()['id']}").json()
    speech = next(s for s in pv["stages"] if s["key"] == "speech")
    assert speech["status"] == "failed" and "Split it" in speech["detail"]


def test_a_slow_voice_splits_the_scene_and_carries_on(client, fast_driver, monkeypatch):
    c = _presenter_character(client, fast_driver)
    # the mock voice reads 2.5 words a second, so these 20 words take 8 s: over a 5 s cap, under it once halved
    monkeypatch.setattr(svc, "MAX_SCENE_S", 5.0)
    text = "When evening comes, the old shepherd walks out, looks at the hills and waits for the first star to rise."
    r = client.post("/api/presenter", json={"character_id": c["id"], "script": text})
    assert len(r.json()["scenes"]) == 1
    _run_all(fast_driver)
    pv = client.get(f"/api/presenter/{r.json()['id']}").json()
    stages = {s["key"]: s["status"] for s in pv["stages"]}
    assert stages["speech"] == "done", pv["stages"]
    assert len(pv["scenes"]) == 2 and " ".join(sc["text"] for sc in pv["scenes"]) == text
    assert all(sc["duration_s"] <= 5.0 for sc in pv["scenes"])


def test_a_cancelled_video_says_so_stops_its_parts_and_resumes_on_retry(client, fast_driver):
    from app.models import Job
    from app.worker import process_one

    c = _presenter_character(client, fast_driver)
    pv = client.post("/api/presenter", json={"character_id": c["id"], "script": SCRIPT, "review": False}).json()
    job_id = pv["job"]["id"]
    # one pass: the voices are queued and the video waits on them in the queue
    process_one(driver_factory=fast_driver)
    with SessionLocal() as db:
        parent = db.get(Job, job_id)
        parts = list(parent.result["waiting_on"])
        assert parent.status == "queued" and parts

    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    with SessionLocal() as db:
        assert {db.get(Job, j).status for j in parts} == {"cancelled"}
    pv = client.get(f"/api/presenter/{pv['id']}").json()
    speech = next(s for s in pv["stages"] if s["key"] == "speech")
    assert speech["status"] == "pending" and speech["detail"] == "Cancelled"

    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    _run_all(fast_driver)
    pv = client.get(f"/api/presenter/{pv['id']}").json()
    assert {s["key"]: s["status"] for s in pv["stages"]}["assemble"] == "done", pv["stages"]
    assert pv["outputs"]["draft"]["duration_s"] > 0
