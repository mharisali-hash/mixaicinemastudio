"""AI video podcast (contract v24, continuous cameras): segments, host track audio and the cut-list rules,
the ltx23_ia2v / ltx23_ia2v_extend graphs, consent, the hidden project, and the whole pipeline on the mock
driver (both review stops, the long-take tracks piece by piece, resume, the cut over the episode mix)."""
import re
import json
import shutil
from pathlib import Path

import httpx
import pytest

from app import audio_analysis as aa
from app import longtake
from app import reel as rl
from app.config import get_settings
from app.db import SessionLocal
from app.drivers.comfy import lipsync_plan, plan_generation
from app.drivers.mock import MockDriver
from app.llm import set_transport
from app.models import Generation, Job, MediaItem, Project, Shot, VideoPodcast
from app.podcast import video as vd
from app.podcast import vplan
from app.workflows import build, check_template, find_node
from tests.test_library import png_bytes, upload
from tests.test_podcast import ff_meta, rms, streams
from tests.test_storyboard import _run_all

FIXTURE = Path(__file__).parent / "fixtures" / "comfy_object_info.json"


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


# ---------------------------------------------------------------- plan rules

def _line(lid, who, start, end, chapter, text="words"):
    return {"line_id": lid, "kind": "speech", "speaker_id": who, "name": who, "text": text, "start": start,
            "end": end, "chapter_id": chapter}


TL = [_line("l1", "a", 4.0, 16.0, "c1"), _line("l2", "b", 16.4, 21.0, "c1"), _line("l3", "a", 21.4, 28.0, "c1"),
      _line("l4", "b", 28.4, 54.0, "c1"), _line("l5", "a", 56.0, 62.0, "c2"), _line("l6", "b", 62.4, 70.0, "c2")]
CHAPTERS = [{"id": "c1", "title": "One", "start": 0.0, "end": 56.0}, {"id": "c2", "title": "Two", "start": 56.0,
                                                                       "end": 74.0}]
TOTAL = 74.0
CAMS = {"a", "b", vplan.WIDE, vplan.COVER}


def _alternating(n=30, length=7.0, gap=0.4):
    out, t = [], 3.0
    for i in range(n):
        out.append(_line(f"x{i}", "ab"[i % 2], t, t + length, "c1"))
        t += length + gap
    return out, t + 3.0


def test_segments_follow_chapters_and_split_long_ones_at_a_line_gap():
    segs = vplan.segments(TL, CHAPTERS, TOTAL)
    assert [(s["start"], s["end"], s["chapter_id"]) for s in segs] == [(0.0, 55.0, "c1"), (55.0, 74.0, "c2")]
    # the chapter edge moves back to the middle of the gap before its first line
    tl, total = _alternating()
    segs = vplan.segments(tl, [{"id": "c1", "title": "All", "start": 0, "end": total}], total, max_s=60.0)
    assert segs[0]["start"] == 0 and segs[-1]["end"] == pytest.approx(total, abs=0.05) and len(segs) == 4
    gaps = {p for p, _ in vplan.gap_points(tl)}
    for x, y in zip(segs, segs[1:]):
        assert x["end"] == y["start"] and x["end"] in gaps  # a sentence gap, never mid-line
    assert all(s["duration"] <= 60.0 + 1e-6 for s in segs)
    # no chapters: the episode is one segment; ids are stable for the same timing
    one = vplan.segments(TL, [], TOTAL, salt="v")
    assert len(one) == 1 and one[0]["id"] == vplan.segments(TL, None, TOTAL, salt="v")[0]["id"]
    # a split prefers a gap where the speaker changes
    same = [_line("m1", "a", 0.5, 30.0, "c"), _line("m2", "a", 30.4, 58.0, "c"), _line("m3", "b", 58.6, 66.0, "c"),
            _line("m4", "b", 66.4, 100.0, "c")]
    assert vplan.segments(same, [], 101.0, max_s=60.0)[0]["end"] == pytest.approx(58.3, abs=0.05)


def test_tracks_one_per_host_per_segment_plus_wide_openings():
    plan = vplan.build(TL, CHAPTERS, TOTAL, ["a", "b"], salt="vp1")
    hosts = [t for t in plan["tracks"] if t["kind"] == "host"]
    assert len(hosts) == 2 * len(plan["segments"])
    for t in hosts:
        seg = next(s for s in plan["segments"] if s["id"] == t["segment_id"])
        assert (t["start"], t["end"]) == (seg["start"], seg["end"]) and t["angle_key"] == f"{t['camera']}:close"
    assert next(t for t in hosts if t["camera"] == "a" and t["start"] == 0)["lines"] == ["l1", "l3"]
    wides = [t for t in plan["tracks"] if t["kind"] == "wide"]
    # intro, the second chapter's opening, the outro; each one LTX piece at most
    assert [w["start"] for w in wides] == [0.0, 55.0, 70.0]
    assert all(w["duration"] <= vplan.WIDE_MAX_S + 1e-6 for w in wides)
    # the same timing gives the same track ids (renders survive a replan), another record gets its own
    again = vplan.build(TL, CHAPTERS, TOTAL, ["a", "b"], salt="vp1")
    assert [t["id"] for t in again["tracks"]] == [t["id"] for t in plan["tracks"]]
    assert not {t["id"] for t in plan["tracks"]} & {t["id"] for t in vplan.build(TL, CHAPTERS, TOTAL, ["a", "b"],
                                                                                  salt="vp2")["tracks"]}


def test_cut_list_rules():
    plan = vplan.build(TL, CHAPTERS, TOTAL, ["a", "b"])
    cuts = plan["cuts"]
    assert vplan.check(cuts, TOTAL, CAMS, vplan.wide_spans(plan)) is None
    assert cuts[0]["start"] == 0 and cuts[-1]["end"] == TOTAL
    assert all(c["duration"] >= vplan.MIN_SHOT_S - 1e-6 for c in cuts)
    assert all(x["camera"] != y["camera"] for x, y in zip(cuts, cuts[1:]))
    # intro, the chapter opening and the outro are wide
    assert (cuts[0]["camera"], cuts[0]["end"]) == ("wide", 4.0) and cuts[-1]["camera"] == "wide"
    opening = next(c for c in cuts if c["start"] == 55.0)
    assert opening["camera"] == "wide" and 2.5 <= opening["duration"] <= vplan.WIDE_MAX_S
    # the speaker's camera while they talk (outside the cutaways)
    at = lambda t: next(c for c in cuts if c["start"] <= t < c["end"])["camera"]  # noqa: E731
    assert at(10.0) == "a" and at(18.0) == "b" and at(50.0) == "b" and at(66.0) == "b"
    # a listener cutaway on the third line, 2.5–4 s, of the host who spoke before
    away = [c for c in cuts if c["camera"] not in ("wide", "cover") and c["speaker_id"] not in (None, c["camera"])]
    assert away and all(vplan.LISTEN_S[0] - 1e-6 <= c["duration"] for c in away)
    # l4 is a 25.6 s turn: somewhere inside it we see host a listening, for at most 4 s
    inside = [c for c in cuts if c["camera"] == "a" and 28.4 < c["start"] and c["end"] < 54.0]
    assert inside and all(c["duration"] <= vplan.LISTEN_S[1] + 1e-6 for c in inside)
    # every cut point sits in a voice gap, or in a pause found inside a line
    pts = set(plan["snaps"])
    for prev, c in zip(cuts, cuts[1:]):
        # off a gap only where a listener cutaway starts or ends, or the chapter's wide opening hands over
        talker = (vplan.item_at(TL, c["start"] + 0.01) or {}).get("speaker_id")
        sides = {prev["camera"], c["camera"]} - {talker}
        assert c["start"] in pts or sides & ({"wide"} | ({"a", "b"} - {talker})), c
    # a pause inside the line pulls the cutaway's edges onto it
    paused = vplan.build(TL, CHAPTERS, TOTAL, ["a", "b"], pauses=[39.5, 43.0])
    edges = {c["start"] for c in paused["cuts"]} | {c["end"] for c in paused["cuts"]}
    assert 39.5 in edges and 43.0 in edges
    # cutaways come every 3–4 lines on a long alternating conversation
    tl, total = _alternating()
    many = vplan.build(tl, [], total, ["a", "b"])
    # each lands on the end of its line (the listener speaks next), so it shows as a cut inside a line
    n_away = sum(1 for c in many["cuts"][1:] if c["start"] not in set(many["snaps"]))
    assert 30 // 4 <= n_away <= 30 // 3
    # a solo show has nobody to cut away to
    solo = vplan.build(TL, CHAPTERS, TOTAL, ["a"])["cuts"]
    assert {c["camera"] for c in solo} <= {"a", "wide"}


def test_cover_ends_switch_in_place_and_edits_are_checked():
    plan = vplan.build(TL, CHAPTERS, TOTAL, ["a", "b"], cover_intro=True, cover_outro=True)
    assert plan["cuts"][0]["camera"] == "cover" and plan["cuts"][-1]["camera"] == "cover"
    # the wide takes are planned anyway, so turning the cover off needs no replan
    back = vplan.apply_cover(plan, False, False)
    assert back[0]["camera"] == "wide" and back[-1]["camera"] == "wide"
    assert vplan.check(back, TOTAL, CAMS, vplan.wide_spans(plan)) is None
    wides = vplan.wide_spans(plan)
    cuts = [dict(c) for c in plan["cuts"]]
    mid = next(i for i, c in enumerate(cuts) if c["start"] >= 20 and c["camera"] in ("a", "b"))
    cuts[mid]["camera"] = "wide"
    assert "wide camera" in vplan.check(cuts, TOTAL, CAMS, wides)
    short = [{**c} for c in plan["cuts"]]
    short[1]["end"] = short[2]["start"] = short[1]["start"] + 1.0
    assert "shorter" in vplan.check(short, TOTAL, CAMS, wides)
    assert "camera" in vplan.check([{**plan["cuts"][0], "camera": "zed", "end": TOTAL}], TOTAL, CAMS, wides)
    # a switch lands on the nearest voice gap, and the next switch is kept
    sw = vplan.switch(plan["cuts"], 16.0, "cover", plan["snaps"])
    c = next(c for c in sw if c["start"] <= 17.0 < c["end"])
    assert (c["start"], c["end"], c["camera"]) == (pytest.approx(16.2083, abs=1e-3), pytest.approx(21.2083, abs=1e-3),
                                                   "cover")


# ---------------------------------------------------------------- the ia2v workflow

def test_ia2v_template_routes_the_speech_and_trims_it():
    info = json.loads(FIXTURE.read_text(encoding="utf-8"))
    res = check_template("ltx23_ia2v", info)
    assert res["ok"], res
    assert res["features"]["two_stage"]["ok"]
    inputs = {"prompt": "Ali talking", "first_image": "mixai/kf.png", "speech_audio": "mixai/speech.wav",
              "audio_start_s": 3.25, "duration_s": 4.0, "width": 1280, "height": 704, "latent_width": 640,
              "latent_height": 352, "num_frames": 97, "seed": 7}
    graph, resolved = build("ltx23_ia2v", inputs)
    load = graph[find_node(graph, "SpeechAudio")]
    assert load["class_type"] == "LoadAudio" and load["inputs"]["audio"] == "mixai/speech.wav"
    trim = graph[find_node(graph, "AudioTrim")]
    assert trim["class_type"] == "TrimAudioDuration"
    assert trim["inputs"]["start_index"] == 3.25 and trim["inputs"]["duration"] == 4.0
    assert trim["inputs"]["audio"] == [find_node(graph, "SpeechAudio"), 0]
    # the speech is frozen: a zero mask over the encoded audio, concatenated with the video latent
    mask = graph[find_node(graph, "AudioMask")]
    assert mask["class_type"] == "SolidMask" and mask["inputs"]["value"] == 0.0
    av = graph[find_node(graph, "AVLatent")]
    assert av["inputs"]["audio_latent"] == [find_node(graph, "AudioFreeze"), 0]
    assert graph[find_node(graph, "VideoLatent")]["inputs"]["length"] == 97
    assert "Upsample" in {n["_meta"]["title"] for n in graph.values()} and resolved["single_stage"] is False

    draft, resolved = build("ltx23_ia2v", {**inputs, "single_stage": True, "latent_width": 832, "latent_height": 480,
                                           "width": 832, "height": 480})
    titles = {n["_meta"]["title"] for n in draft.values()}
    assert not titles & {"Upsample", "SpatialLoader", "FirstFrame2", "AVLatent2", "Sampler2", "Separate2", "Guider2",
                         "Noise2", "Sigmas2", "CropGuides"}
    assert draft[find_node(draft, "Decode")]["inputs"]["samples"] == [find_node(draft, "Separate"), 0]
    assert resolved["single_stage"] is True


class _Lookup:
    def __init__(self, root):
        self.root = root

    def generation_file(self, gid):
        p = self.root / f"{gid}.png"
        p.write_bytes(png_bytes())
        return p

    def project_aspect(self, _pid):
        return "16:9"


def test_lipsync_plan_draft_and_final(tmp_path):
    speech = tmp_path / "speech.wav"
    params = {"kind": "take", "first_frame_id": "kf1", "speech_audio": str(speech), "audio_start_s": 1.5,
              "num_frames": 121, "fps": 24, "aspect_ratio": "16:9"}
    p = plan_generation("take", "Ali talking", params, 3, _Lookup(tmp_path))
    assert p.template == "ltx23_ia2v" and p.images["speech_audio"] == speech
    assert p.inputs["single_stage"] is True and (p.inputs["width"], p.inputs["latent_width"]) == (832, 832)
    assert p.inputs["audio_start_s"] == 1.5 and p.inputs["duration_s"] == 5.0  # (121 - 1) / 24
    hq = lipsync_plan("x", {**params, "quality": "hq"}, 3, _Lookup(tmp_path), "", [])
    assert hq.inputs["single_stage"] is False
    assert (hq.inputs["width"], hq.inputs["height"], hq.inputs["latent_width"]) == (1280, 704, 640)
    # a take without speech is still plain i2v
    assert plan_generation("take", "x", {k: v for k, v in params.items() if k != "speech_audio"}, 3,
                           _Lookup(tmp_path)).template == "ltx23_i2v"


def test_ia2v_extend_template_continues_the_context_with_frozen_speech_and_a_light_guide():
    info = json.loads(FIXTURE.read_text(encoding="utf-8"))
    res = check_template("ltx23_ia2v_extend", info)
    assert res["ok"], res
    assert res["features"]["two_stage"]["ok"]
    inputs = {"prompt": "Ali in conversation", "context_video": "mixai/tail.mp4", "guide_image": "mixai/kf.png",
              "speech_audio": "mixai/track.wav", "audio_start_s": 9.625, "duration_s": 10.0, "width": 1280,
              "height": 704, "latent_width": 640, "latent_height": 352, "num_frames": 241, "seed": 7}
    graph, resolved = build("ltx23_ia2v_extend", inputs)
    node = lambda t: graph[find_node(graph, t)]  # noqa: E731
    assert node("ContextVideo")["class_type"] == "VHS_LoadVideo" and node("ContextVideo")["inputs"]["video"] == "mixai/tail.mp4"
    # the tail lies in place over the first latent frames, at the first pass's size
    assert node("ContextFrames")["inputs"]["image"] == [find_node(graph, "ContextScale"), 0]
    assert node("ContextScale")["inputs"]["width"] == 640 and node("ContextScale2")["inputs"]["width"] == 1280
    # the whole window of the track audio, frozen
    trim = node("AudioTrim")
    assert trim["inputs"]["start_index"] == 9.625 and trim["inputs"]["duration"] == 10.0
    assert node("AudioMask")["inputs"]["value"] == 0.0
    assert node("AVLatent")["inputs"]["audio_latent"] == [find_node(graph, "AudioFreeze"), 0]
    # the keyframe guides the last frame lightly and is cropped off before the second pass and the decode
    guide = node("KeyGuide")
    assert guide["class_type"] == "LTXVAddGuide" and guide["inputs"]["frame_idx"] == -1
    assert guide["inputs"]["strength"] == pytest.approx(0.2)
    assert node("Upsample")["inputs"]["samples"] == [find_node(graph, "CropGuides"), 2]
    assert node("Guider")["inputs"]["positive"] == [find_node(graph, "KeyGuide"), 0]
    assert resolved["single_stage"] is False and node("VideoLatent")["inputs"]["length"] == 241

    draft, resolved = build("ltx23_ia2v_extend", {**inputs, "single_stage": True, "width": 832, "height": 480,
                                                  "latent_width": 832, "latent_height": 480})
    titles = {n["_meta"]["title"] for n in draft.values()}
    assert not titles & {"Upsample", "SpatialLoader", "ContextFrames2", "ContextScale2", "AVLatent2", "Sampler2",
                         "Separate2", "Guider2", "Noise2", "Sigmas2"}
    assert draft[find_node(draft, "Decode")]["inputs"]["samples"] == [find_node(draft, "CropGuides"), 2]
    # without a keyframe the guide drops out and the conditioning goes straight through
    bare, _ = build("ltx23_ia2v_extend", {k: v for k, v in inputs.items() if k != "guide_image"})
    assert "KeyGuide" not in {n["_meta"]["title"] for n in bare.values()}
    assert bare[find_node(bare, "AVLatent")]["inputs"]["video_latent"] == [find_node(bare, "ContextFrames"), 0]


def test_track_pieces_route_to_ia2v_then_ia2v_extend(tmp_path):
    speech, tail = tmp_path / "track.wav", tmp_path / "tail.mp4"
    first = {"kind": "take_chunk", "chunk_idx": 0, "first_frame_id": "kf1", "speech_audio": str(speech),
             "audio_start_s": 0.0, "num_frames": 241, "fps": 24, "aspect_ratio": "16:9"}
    p = plan_generation("take_chunk", "Ali", first, 3, _Lookup(tmp_path))
    assert p.template == "ltx23_ia2v" and p.inputs["audio_start_s"] == 0.0 and p.inputs["single_stage"] is True
    later = {**first, "chunk_idx": 1, "first_frame_id": None, "context_video": str(tail), "context_frames": 9,
             "guide_frame_id": "kf1", "audio_start_s": 9.625, "quality": "hq"}
    p = plan_generation("take_chunk", "Ali", later, 3, _Lookup(tmp_path))
    assert p.template == "ltx23_ia2v_extend" and p.images["context_video"] == tail
    assert p.images["guide_image"].name == "kf1.png" and p.images["speech_audio"] == speech
    assert p.inputs["audio_start_s"] == 9.625 and p.inputs["duration_s"] == 10.0  # (241 - 1) / 24
    assert (p.inputs["width"], p.inputs["latent_width"], p.inputs["single_stage"]) == (1280, 640, False)
    # an ordinary long take's chunk is untouched
    plain = {k: v for k, v in later.items() if k not in ("speech_audio",)}
    assert plan_generation("take_chunk", "x", plain, 3, _Lookup(tmp_path)).template == "ltx23_extend"


# ---------------------------------------------------------------- record, consent, hidden project

@pytest.fixture
def fake_looks():
    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
        content = json.dumps({"description": "early thirties, short dark hair, warm brown skin, navy shirt"}) \
            if schema.get("title") == "HostLook" else "ok"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                         "usage": {}})

    set_transport(httpx.MockTransport(answer))
    yield
    set_transport(None)


DIALOGUE = "# Welcome\nAli: Hello and welcome to the show today friends.\nSara: Thanks Ali, glad to be here " \
           "with you.\n# The news\nAli: Big news this week from the river town.\nSara: The bridge finally opened."


def _episode(client, fast_driver, mix=True, text=DIALOGUE):
    ep = ok(client.post("/api/podcast/episodes", json={
        "title": "River news", "language": "en",
        "hosts": [{"name": "Ali", "role": "host", "persona": "curious"},
                  {"name": "Sara", "role": "cohost", "persona": "dry wit"}]}), 201)
    ep = ok(client.post(f"/api/podcast/episodes/{ep['id']}/script", json={"text": text}))
    if mix:
        st = {**ep["settings"], "loudness": "none"}
        ok(client.patch(f"/api/podcast/episodes/{ep['id']}", json={"settings": st}))
        ok(client.post(f"/api/podcast/episodes/{ep['id']}/render"), 202)
        _run_all(fast_driver)
        ok(client.post(f"/api/podcast/episodes/{ep['id']}/mix"), 202)
        _run_all(fast_driver)
    return ok(client.get(f"/api/podcast/episodes/{ep['id']}"))


def test_create_makes_a_hidden_project_with_a_character_per_host(client, fast_driver):
    ep = _episode(client, fast_driver, mix=False)
    assert client.get(f"/api/podcast/episodes/{ep['id']}/ai-video").status_code == 404
    vp = ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={"aspect": "16:9"}), 201)
    again = ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={}), 200)
    assert again["id"] == vp["id"]
    assert [h["name"] for h in vp["hosts"]] == ["Ali", "Sara"] and all(h["character_id"] for h in vp["hosts"])
    assert vp["stage"] == "cast" and vp["status"] == "idle"
    with SessionLocal() as db:
        assert db.get(Project, vp["project_id"]).authoring_mode == "podcast"
    assert vp["project_id"] not in {p["id"] for p in ok(client.get("/api/projects"))}
    assert vp["project_id"] not in {p["id"] for p in ok(client.get("/api/dashboard"))["recent_projects"]}
    # an ordinary project's character can be borrowed
    pr = ok(client.post("/api/projects", json={"title": "Film", "authoring_mode": "scene_by_scene"}), 201)
    ok(client.post(f"/api/projects/{pr['id']}/characters", json={"name": "Diver", "description": "Grey hair"}), 201)
    names = {c["name"] for c in ok(client.get(f"/api/podcast/ai-video/{vp['id']}/characters"))}
    assert names == {"Diver"}


def test_uploaded_photo_needs_consent(client, fast_driver):
    ep = _episode(client, fast_driver, mix=False)
    vp = ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={}), 201)
    photo = ok(upload(client, "ali.png", png_bytes(), "image/png"), 201)
    hid = vp["hosts"][0]["host_id"]
    url = f"/api/podcast/ai-video/{vp['id']}"
    body = {"source": "photo", "media_id": photo["id"]}
    assert client.patch(url, json={"hosts": {hid: body}}).status_code == 422
    assert client.patch(url, json={"hosts": {hid: {**body, "consent": {"name": "Ali Raza",
                                                                      "statement": "yes"}}}}).status_code == 422
    assert client.patch(url, json={"hosts": {hid: {**body, "consent": {"name": "",
                                                                      "statement": vd.CONSENT}}}}).status_code == 422
    vp = ok(client.patch(url, json={"hosts": {hid: {**body, "consent": {"name": "Ali Raza", "statement": vd.CONSENT}}}}))
    host = vp["hosts"][0]
    assert host["source"] == "photo" and host["photo_media_id"] == photo["id"]
    assert host["consent"]["name"] == "Ali Raza" and host["consent"]["statement"] == vd.CONSENT
    assert host["consent"]["at"] and host["consent"]["user_id"]


def test_uploaded_photo_as_the_portrait(client, fast_driver):
    ep = _episode(client, fast_driver, mix=False)
    vp = ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={}), 201)
    photo = ok(upload(client, "ali.png", png_bytes(), "image/png"), 201)
    hid = vp["hosts"][0]["host_id"]
    choice = {"source": "photo", "media_id": photo["id"], "as_is": True,
              "consent": {"name": "Ali Raza", "statement": vd.CONSENT}}
    vp = ok(client.patch(f"/api/podcast/ai-video/{vp['id']}", json={"hosts": {hid: choice}}))
    host = vp["hosts"][0]
    # approved straight away, so the cast stage keeps it and only turns it for the sheet views
    assert host["photo_as_is"] and host["portrait"]["status"] == "approved"
    assert host["portrait"]["params"]["source"] == "upload" and host["portrait"]["params"]["vp_rev"] == 2


# ---------------------------------------------------------------- the whole pipeline

def _continue(client, vp, fast_driver):
    job = ok(client.post(f"/api/podcast/ai-video/{vp['id']}/continue"), 202)
    assert job["type"] == "video_podcast" and job["lane"] == "general"
    _run_all(fast_driver)
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    assert vp["job"]["status"] == "done", vp["job"]
    return vp


def _ready_vp(client, fast_driver, text=DIALOGUE, **settings):
    ep = _episode(client, fast_driver, text=text)
    vp = ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={"aspect": "16:9"}), 201)
    if settings:
        vp = ok(client.patch(f"/api/podcast/ai-video/{vp['id']}", json={"settings": settings}))
    vp = _continue(client, vp, fast_driver)
    assert vp["status"] == "review" and vp["review"] == "hosts" and vp["stage"] == "cast"
    assert all(h["portrait"]["status"] == "ready" for h in vp["hosts"])
    assert all(set(h["sheet_views"]) == {"front", "three_quarter"} for h in vp["hosts"])
    vp = _continue(client, vp, fast_driver)
    assert vp["review"] == "look" and vp["stage"] == "keyframes"
    assert all(h["portrait"]["status"] == "approved" for h in vp["hosts"])
    assert vp["set"]["establishing"]["status"] == "approved"
    assert len(vp["angles"]) == 2 * 3 + 2 and all(a["frame"]["status"] == "ready" for a in vp["angles"])
    # the angle frames go through the edit model with the host and the set as references
    close = next(a for a in vp["angles"] if a["key"].endswith(":close"))
    refs = close["frame"]["params"]["reference_ids"]
    assert refs[0] == vp["hosts"][0]["portrait"]["id"] and refs[-1] == vp["set"]["establishing"]["id"]
    vp = _continue(client, vp, fast_driver)
    assert vp["status"] == "idle" and vp["stage"] == "render"
    assert all(a["frame"]["status"] == "approved" for a in vp["angles"])
    assert vp["plan"]["cuts"][-1]["end"] == pytest.approx(vp["duration_s"], abs=0.05)
    assert client.post(f"/api/podcast/ai-video/{vp['id']}/continue").status_code == 409
    return ep, vp


def _take_count(project_id):
    with SessionLocal() as db:
        return db.query(Generation).filter(Generation.project_id == project_id, Generation.kind == "take").count()


class _Recorder(MockDriver):
    calls: list = []

    def generate_video(self, prompt, params, seed, out_path, progress_cb):
        _Recorder.calls.append(dict(params))
        return super().generate_video(prompt, params, seed, out_path, progress_cb)


@pytest.fixture
def recorder(monkeypatch):
    # short pieces, so the mock episode's tracks are long takes of several pieces
    monkeypatch.setattr(vd, "PIECE_S", 2.0)
    monkeypatch.setattr(longtake, "SINGLE_MAX_FRAMES", 49)
    _Recorder.calls = []
    return lambda: _Recorder(step_seconds=0)


def _host_tracks(vp):
    return [t for t in vp["plan"]["tracks"] if t["kind"] == "host"]


def test_track_audio_is_one_hosts_voice_on_the_segment_clock(client, fast_driver, fake_looks):
    ep, vp = _ready_vp(client, fast_driver, subtitles="none")
    timeline = ep["outputs"]["mix"]["timeline"]
    with SessionLocal() as db:
        row = db.get(VideoPodcast, vp["id"])
        episode = vd.episode_of(db, row)
        takes = vd.line_takes(db, episode)
        for t in vd.tracks_of(row):
            if t["kind"] != "host":
                continue
            path = vd.track_audio(db, row, t, timeline, takes)
            assert path.parent == vd.track_folder(row)
            assert abs(aa.stream_info(path).duration - t["duration"]) < 0.01
            for it in timeline:
                if it["kind"] != "speech" or it["end"] <= t["start"] or it["start"] >= t["end"]:
                    continue
                mid, span = (it["start"] + it["end"]) / 2 - t["start"], min(0.6, (it["end"] - it["start"]) / 3)
                level = rms(path, mid - span / 2, span)
                if it["speaker_id"] == t["host_id"]:
                    assert level > -40, (it, level)
                else:
                    assert level < -80, (it, level)  # the other host's line is silence on this track
            # the same inputs reuse the file
            assert vd.track_audio(db, row, t, timeline, takes) == path


def test_pipeline_renders_continuous_cameras_and_cuts_them_over_the_mix(client, recorder, fake_looks):
    ep, vp = _ready_vp(client, recorder, subtitles="soft")
    plan = vp["plan"]
    assert plan["segments"] and plan["cuts"] and plan["cuts"][-1]["end"] == pytest.approx(vp["duration_s"], abs=0.05)
    assert {c["key"] for c in plan["cameras"]} == {h["host_id"] for h in vp["hosts"]} | {"wide", "cover"}
    hosts = _host_tracks(vp)
    assert len(hosts) == 2 * len(plan["segments"]) and all(t["pieces"] >= 2 for t in hosts)
    est = ok(client.get(f"/api/podcast/ai-video/{vp['id']}/estimate?quality=draft"))
    used = [t for t in plan["tracks"] if t["used"]]
    assert est["tracks"] == len(used) and est["pieces"] == sum(t["pieces"] for t in used)
    assert est["high_s"] > est["low_s"] > 0 and "video GPU" in est["label"]
    assert ok(client.get(f"/api/podcast/ai-video/{vp['id']}/estimate?quality=final"))["low_s"] > est["low_s"]

    job = ok(client.post(f"/api/podcast/ai-video/{vp['id']}/render", json={"quality": "draft"}), 202)
    _run_all(recorder)
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    assert vp["job"]["id"] == job["id"] and vp["job"]["status"] == "done", vp["job"]
    assert vp["stage"] == "done"
    for t in _host_tracks(vp):
        assert t["progress"]["draft"] == {"pieces": t["pieces"], "done": t["pieces"], "current": None,
                                          "failed": None, "status": "ready"}
        assert not t["needs"]["draft"] and t["needs"]["final"]

    # piece 1 of a track is ia2v from the angle keyframe; the rest continue its tail with the audio window frozen
    pieces = [c for c in _Recorder.calls if c.get("kind") == "take_chunk"]
    assert pieces and all(c["speech_audio"].endswith(".wav") for c in pieces)
    lookup = _Lookup(get_settings().data_dir)
    for c in pieces:
        p = plan_generation("take_chunk", "x", c, 1, lookup)
        if c["chunk_idx"] == 0:
            assert p.template == "ltx23_ia2v" and c["audio_start_s"] == 0 and c["first_frame_id"]
        else:
            assert p.template == "ltx23_ia2v_extend" and c["context_video"] and c["guide_frame_id"]
            assert c["audio_start_s"] > 0 and c["context_frames"] == 9
    with SessionLocal() as db:
        takes = db.query(Generation).filter(Generation.project_id == vp["project_id"], Generation.kind == "take").all()
        for g in takes:
            if g.params.get("longtake"):
                chunks = g.params["chunks"]
                for prev, cur in zip(chunks, chunks[1:]):
                    hit = next(c for c in pieces if c["generation_id"] == f"{g.id}_c{cur['idx']}")
                    assert hit["audio_start_s"] == pytest.approx(cur["start_frame"] / 24, abs=1e-3)
                    assert cur["start_frame"] == prev["start_frame"] + prev["frames"] - 9
                # each piece carries the words its host says in it, or listens with the mouth closed
                said = [re.search(r'says: "([^"]*)"', c["prompt"]) for c in chunks]
                assert any(said) and all(m or "mouth closed" in c["prompt"] for m, c in zip(said, chunks))
        out = vp["outputs"]["draft"]
        mix_path = get_settings().data_dir / db.get(Generation, ep["outputs"]["mix"]["generation_id"]).file_path
        video = get_settings().data_dir / db.get(Generation, out["generation_id"]).file_path
        assert {db.get(Job, g.job_id).lane for g in takes} == {"video"}
        shot_rows = db.query(Shot).filter(Shot.project_id == vp["project_id"]).count()
    assert shot_rows == len(vp["plan"]["tracks"]) + len(vp["angles"])
    # as long as the episode, its sound is the episode mix (the tracks' own audio is muted), chapters kept
    assert out["subtitles"] == "soft" and (out["width"], out["height"]) == (832, 480)
    assert abs(aa.stream_info(video).duration - ep["outputs"]["mix"]["duration_s"]) < 0.15
    kinds = streams(video)
    assert (kinds.count("Video"), kinds.count("Audio"), kinds.count("Subtitle")) == (1, 1, 1)
    for t in (0.5, ep["outputs"]["mix"]["duration_s"] / 2):
        assert abs(rms(video, t, 0.8) - rms(mix_path, t, 0.8)) < 1.5
    meta = ff_meta(video)
    assert "title=Welcome" in meta and "title=The news" in meta

    # nothing changed: a second draft has nothing to do
    assert ok(client.get(f"/api/podcast/ai-video/{vp['id']}/estimate?quality=draft"))["tracks"] == 0


def test_resume_from_a_piece_and_final_rerenders_only_changed_tracks(client, recorder, fake_looks):
    ep, vp = _ready_vp(client, recorder, subtitles="none")
    ok(client.post(f"/api/podcast/ai-video/{vp['id']}/render", json={"quality": "draft"}), 202)
    _run_all(recorder)
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    track = max(_host_tracks(vp), key=lambda t: t["pieces"])
    before = _take_count(vp["project_id"])
    old = track["takes"]["draft"]

    # from piece 2: piece 1 is kept from the last take, only the rest go to the GPU
    _Recorder.calls = []
    url = f"/api/podcast/ai-video/{vp['id']}/render"
    assert client.post(url, json={"quality": "draft", "track_ids": [track["id"]], "from_piece": 2,
                                  }).status_code == 202
    _run_all(recorder)
    assert _take_count(vp["project_id"]) == before + 1
    assert sorted(c["chunk_idx"] for c in _Recorder.calls if c.get("kind") == "take_chunk") == \
        list(range(1, track["pieces"]))
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    new = next(t for t in vp["plan"]["tracks"] if t["id"] == track["id"])["takes"]["draft"]
    assert new["id"] != old["id"] and new["parent_id"] == old["id"] and new["status"] == "ready"
    assert new["params"]["chunks"][0]["file"].endswith(f"{new['id']}/chunk_0.mp4")
    assert client.post(url, json={"quality": "draft", "track_ids": [track["id"]], "from_piece": 99}).status_code == 422
    assert client.post(url, json={"quality": "final", "track_ids": [track["id"]], "from_piece": 2}).status_code == 409

    # a failed piece is retried from where it broke
    with SessionLocal() as db:
        g = db.get(Generation, new["id"])
        chunks = [dict(c) for c in g.params["chunks"]]
        chunks[-1].update(status="failed")
        g.params = {**g.params, "chunks": chunks}
        g.status = "failed"
        db.commit()
        assert vd.first_unfinished(g) == len(chunks) - 1
    _Recorder.calls = []
    ok(client.post(url, json={"quality": "draft"}), 202)
    _run_all(recorder)
    assert [c["chunk_idx"] for c in _Recorder.calls if c.get("kind") == "take_chunk"] == [len(chunks) - 1]
    assert ok(client.get(f"/api/podcast/ai-video/{vp['id']}/estimate?quality=draft"))["tracks"] == 0

    # the first final makes every camera; after a new close-up of one host only that host's tracks need it again
    ok(client.post(url, json={"quality": "final"}), 202)
    _run_all(recorder)
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    assert vp["outputs"]["final"] and not vp["outputs"]["final"]["fallback_shots"]
    host = vp["hosts"][0]["host_id"]
    angle = next(a for a in vp["angles"] if a["key"] == f"{host}:close")
    g = ok(client.post(f"/api/podcast/ai-video/{vp['id']}/regenerate", json={"what": "angle", "key": angle["key"]}),
           202)
    # from today's single-person template, not the old frame's prompt
    assert "nobody else" in g["prompt"] and "towards the co-host" not in g["prompt"]
    _run_all(recorder)
    # the page shows the new one waiting for its Approve, not the approved old one
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    shown = next(a for a in vp["angles"] if a["key"] == angle["key"])["frame"]
    assert shown["id"] == g["id"] and shown["status"] == "ready"
    ok(client.post(f"/api/generations/{g['id']}/approve"))
    assert ok(client.post(f"/api/podcast/ai-video/{vp['id']}/continue"), 409)  # the new frame counts as current
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    need = [t for t in vp["plan"]["tracks"] if t["needs"]["final"]]
    assert need and {t["camera"] for t in need} == {host}
    assert ok(client.get(f"/api/podcast/ai-video/{vp['id']}/estimate?quality=final"))["tracks"] == len(need)
    count = _take_count(vp["project_id"])
    ok(client.post(url, json={"quality": "final"}), 202)
    _run_all(recorder)
    assert _take_count(vp["project_id"]) == count + len(need)


LONG = "# Welcome\n" + "\n".join(
    f"{'Ali' if n % 2 == 0 else 'Sara'}: " + "This is a longer thought about the river town and its bridge. " * 2
    for n in range(6))


def test_cut_list_edits_need_no_render(client, fast_driver, fake_looks):
    ep, vp = _ready_vp(client, fast_driver, text=LONG, subtitles="none")
    base = f"/api/podcast/ai-video/{vp['id']}"
    ok(client.post(f"{base}/render", json={"quality": "draft"}), 202)
    _run_all(fast_driver)
    vp = ok(client.get(base))
    takes = _take_count(vp["project_id"])
    cuts = vp["plan"]["cuts"]
    a, b = (h["host_id"] for h in vp["hosts"])
    target = next(c for c in cuts if c["camera"] in (a, b) and c["duration"] >= 2 * vplan.MIN_SHOT_S + 0.5)
    other = b if target["camera"] == a else a
    at = target["start"] + target["duration"] / 2
    vp = ok(client.post(f"{base}/cuts/switch", json={"at": at, "camera": other}))
    new = vp["plan"]["cuts"]
    assert len(new) >= len(cuts) and any(c["camera"] == other and c["start"] > target["start"] for c in new)
    # too close to the cut's start leaves a sliver: refused
    first = next(c for c in new if c["start"] > 0 and c["camera"] in (a, b))
    assert client.post(f"{base}/cuts/switch", json={"at": first["start"] + 0.5,
                                                    "camera": "wide"}).status_code == 422
    cut = next(c for c in new if c["camera"] in (a, b))
    vp = ok(client.patch(f"{base}/cuts/{cut['id']}", json={"camera": other if cut["camera"] == a else a}))
    assert client.patch(f"{base}/cuts/{cut['id']}", json={"camera": "nobody"}).status_code == 422
    n = len(vp["plan"]["cuts"])
    # the intro's wide take can't run on over the next cut
    assert client.delete(f"{base}/cuts/{vp['plan']['cuts'][1]['id']}").status_code == 422
    gone = next(c for p, c in zip(vp["plan"]["cuts"], vp["plan"]["cuts"][1:]) if p["camera"] in (a, b))
    vp = ok(client.delete(f"{base}/cuts/{gone['id']}"))
    assert len(vp["plan"]["cuts"]) < n and gone["id"] not in {c["id"] for c in vp["plan"]["cuts"]}
    rows = [{k: c[k] for k in ("id", "camera", "start", "end")} for c in vp["plan"]["cuts"]]
    rows[1]["start"] += 0.5
    assert client.patch(base, json={"cuts": rows}).status_code == 422
    # none of that needed a render, and the cut still assembles from the tracks already made
    assert not any(t["needs"]["draft"] for t in vp["plan"]["tracks"] if t["used"] and t["kind"] == "host")
    ok(client.post(f"{base}/assemble", json={"quality": "draft"}), 202)
    _run_all(fast_driver)
    vp = ok(client.get(base))
    assert vp["job"]["status"] == "done", vp["job"]
    assert _take_count(vp["project_id"]) == takes


def test_final_cut_burns_subtitles_and_falls_back_to_draft_tracks(client, fast_driver, fake_looks, monkeypatch):
    from app import subtitles

    ep, vp = _ready_vp(client, fast_driver, subtitles="burn", cover_intro=True)
    ok(client.post(f"/api/podcast/ai-video/{vp['id']}/render", json={"quality": "draft"}), 202)
    calls = []

    def burn(src, dst, cues, *, language=None, width=None, height=None, timeout=7200, encode=None):
        calls.append({"language": language, "size": (width, height), "cues": list(cues)})
        shutil.copyfile(src, dst)
        return {"renderer": "ass", "fonts_missing": ["Noto Nastaliq Urdu"]}

    monkeypatch.setattr(subtitles, "burn", burn)
    _run_all(fast_driver)
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    assert vp["outputs"]["draft"]["renderer"] == "ass" and vp["outputs"]["draft"]["fonts_missing"]
    assert calls and calls[0]["language"] == "en" and calls[0]["size"] == (832, 480)
    assert any("welcome" in c[2].lower() for c in calls[0]["cues"])
    # a final cut with no final tracks yet uses the draft ones and says which cuts did
    ok(client.post(f"/api/podcast/ai-video/{vp['id']}/assemble", json={"quality": "final"}), 202)
    _run_all(fast_driver)
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    fin = vp["outputs"]["final"]
    assert fin and fin["fallback_shots"] and (fin["width"], fin["height"]) == (1280, 704)
    with SessionLocal() as db:
        video = get_settings().data_dir / db.get(Generation, fin["generation_id"]).file_path
        assert rl.probe(video).width == 1280


def test_an_old_per_line_plan_is_rebuilt_on_read(client, fast_driver, fake_looks):
    ep, vp = _ready_vp(client, fast_driver, subtitles="none")
    with SessionLocal() as db:
        row = db.get(VideoPodcast, vp["id"])
        row.plan = [{"id": "old", "type": "lipsync", "angle": "x:close", "start": 0.0, "end": 3.0}]
        db.commit()
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    assert vp["plan"]["tracks"] and vp["plan"]["cuts"] and vp["plan_stats"]["host_tracks"] == 2 * len(
        vp["plan"]["segments"])
    with SessionLocal() as db:
        assert isinstance(db.get(VideoPodcast, vp["id"]).plan, dict)


def test_0017_adds_the_video_podcast_table(tmp_path):
    import sqlite3

    from tests.test_migrations import _alembic

    db = tmp_path / "prod-copy.db"
    _alembic(db, "upgrade", "0017")
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(video_podcast)")}
    assert {"id", "workspace_id", "episode_id", "project_id", "stage", "stages", "settings", "hosts",
            "set_location_id", "plan", "outputs", "job_id"} <= cols
    con.close()
    _alembic(db, "downgrade", "0016")
    con = sqlite3.connect(db)
    assert "video_podcast" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    _alembic(db, "upgrade", "head")


# ---------------------------------------------------------------- Podcast Studio: audio or video, Character hosts

def _studio_character(client, fast_driver, name="Ayesha", look=True):
    c = ok(client.post("/api/characters", json={"name": name, "description": "short dark hair"}), 201)
    ok(client.post("/api/generations", json={"target_type": "character", "target_id": c["id"], "kind": "portrait",
                                             "prompt": "head and shoulders portrait"}), 201)
    _run_all(fast_driver)
    if not look:
        return c, None
    lk = ok(client.post(f"/api/characters/{c['id']}/looks", json={"name": "Office", "outfit": "a navy suit"}), 201)
    _run_all(fast_driver)
    return c, lk


def test_a_video_episode_has_its_ai_video_from_the_start(client):
    hosts = [{"name": "Ali"}, {"name": "Sara"}]
    audio = ok(client.post("/api/podcast/episodes", json={"title": "Plain", "hosts": hosts}), 201)
    video = ok(client.post("/api/podcast/episodes", json={"title": "On camera", "medium": "video", "hosts": hosts}), 201)
    assert audio["medium"] == "audio" and video["medium"] == "video"
    assert client.get(f"/api/podcast/episodes/{audio['id']}/ai-video").status_code == 404
    vp = ok(client.get(f"/api/podcast/episodes/{video['id']}/ai-video"))
    assert [h["name"] for h in vp["hosts"]] == ["Ali", "Sara"]
    # the Produce step writes settings whole and knows nothing of the medium
    st = {**video["settings"], "loudness": "none"}
    assert ok(client.patch(f"/api/podcast/episodes/{video['id']}", json={"settings": st, "step": 9}))["medium"] == "video"
    listed = {e["id"]: e["medium"] for e in ok(client.get("/api/podcast/episodes"))}
    assert listed == {audio["id"]: "audio", video["id"]: "video"}

    # one way only
    assert client.patch(f"/api/podcast/episodes/{video['id']}", json={"medium": "audio"}).status_code == 422
    turned = ok(client.patch(f"/api/podcast/episodes/{audio['id']}", json={"medium": "video"}))
    assert turned["medium"] == "video"
    assert ok(client.get(f"/api/podcast/episodes/{audio['id']}/ai-video"))["episode_id"] == audio["id"]


def test_an_old_audio_episode_with_an_ai_video_counts_as_video(client, fast_driver):
    ep = _episode(client, fast_driver, mix=False)
    ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={}), 201)
    assert ok(client.get(f"/api/podcast/episodes/{ep['id']}"))["medium"] == "video"
    assert ok(client.get("/api/podcast/episodes"))[0]["medium"] == "video"


def test_a_host_can_be_a_character_studio_character_in_one_of_its_looks(client, fast_driver):
    c, lk = _studio_character(client, fast_driver)
    other, other_look = _studio_character(client, fast_driver, name="Bilal")
    host = {"name": "Ayesha", "character_id": c["id"], "look_id": lk["id"]}
    ep = ok(client.post("/api/podcast/episodes", json={"title": "Ep", "medium": "video",
                                                       "hosts": [host, {"name": "Sara"}]}), 201)
    assert ep["hosts"][0]["character_id"] == c["id"] and ep["hosts"][0]["look_id"] == lk["id"]
    # the look's picture is the host's approved portrait, copied into the video's own project
    vp = ok(client.get(f"/api/podcast/episodes/{ep['id']}/ai-video"))
    h = vp["hosts"][0]
    assert h["source"] == "studio" and h["from_character_id"] == c["id"] and h["look_id"] == lk["id"]
    assert h["portrait"]["status"] == "approved" and h["portrait"]["params"]["copied_from"] == lk["picture"]["id"]
    assert vp["hosts"][1]["source"] == "persona"
    assert client.post(f"/api/podcast/ai-video/{vp['id']}/regenerate",
                       json={"what": "portrait", "key": h["host_id"]}).status_code == 409

    # someone else's look, an unknown character and a project character are refused
    bad = {**host, "look_id": other_look["id"]}
    assert client.patch(f"/api/podcast/episodes/{ep['id']}", json={"hosts": [bad]}).status_code == 422
    gone = {**host, "character_id": "nope"}
    assert client.patch(f"/api/podcast/episodes/{ep['id']}", json={"hosts": [gone]}).status_code == 404
    pr = ok(client.post("/api/projects", json={"title": "Film", "authoring_mode": "scene_by_scene"}), 201)
    pc = ok(client.post(f"/api/projects/{pr['id']}/characters", json={"name": "Diver"}), 201)
    assert client.patch(f"/api/podcast/episodes/{ep['id']}",
                        json={"hosts": [{"name": "Diver", "character_id": pc["id"]}]}).status_code == 422

    # switching the episode host to another character follows through to the video and redoes the cast
    hosts = ok(client.get(f"/api/podcast/episodes/{ep['id']}"))["hosts"]
    hosts[0] = {**hosts[0], "character_id": other["id"], "look_id": None}
    ok(client.patch(f"/api/podcast/episodes/{ep['id']}", json={"hosts": hosts}))
    vp = ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))
    assert vp["hosts"][0]["from_character_id"] == other["id"] and vp["hosts"][0]["look_id"] is None
    # unlinking goes back to a face from the persona
    hosts[0] = {**hosts[0], "character_id": None}
    ok(client.patch(f"/api/podcast/episodes/{ep['id']}", json={"hosts": hosts}))
    assert ok(client.get(f"/api/podcast/ai-video/{vp['id']}"))["hosts"][0]["source"] == "persona"


def test_a_studio_host_picked_in_the_cast_step(client, fast_driver):
    c, lk = _studio_character(client, fast_driver)
    plain, _ = _studio_character(client, fast_driver, name="Bilal", look=False)
    ep = _episode(client, fast_driver, mix=False)
    vp = ok(client.post(f"/api/podcast/episodes/{ep['id']}/ai-video", json={}), 201)
    a, b = (h["host_id"] for h in vp["hosts"])
    vp = ok(client.patch(f"/api/podcast/ai-video/{vp['id']}", json={"hosts": {
        a: {"source": "studio", "character_id": c["id"], "look_id": lk["id"]},
        b: {"source": "studio", "character_id": plain["id"]}}}))
    assert vp["hosts"][0]["portrait"]["params"]["copied_from"] == lk["picture"]["id"]
    # no look: the character's own portrait
    assert vp["hosts"][1]["source"] == "studio" and vp["hosts"][1]["portrait"]["status"] == "approved"
    # a look still being drawn can't be worn yet
    busy = ok(client.post(f"/api/characters/{c['id']}/looks", json={"name": "Garden", "outfit": "linen"}), 201)
    r = client.patch(f"/api/podcast/ai-video/{vp['id']}", json={"hosts": {
        a: {"source": "studio", "character_id": c["id"], "look_id": busy["id"]}}})
    assert r.status_code == 409 and "no finished picture" in r.json()["detail"]
