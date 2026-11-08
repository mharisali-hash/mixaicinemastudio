"""Poetry Shorts (contract v33): the stage pipeline, the line timing fit, scene prompts and refs, the cut."""
import json
import re

import httpx
import numpy as np
import pytest

from app import montage, subtitles
from app.config import get_settings
from app.db import SessionLocal
from app.drivers.mock import MockDriver
from app.llm import set_transport
from app.models import Character, Generation, Job, MediaItem, PoetryShort
from app.poetry import render as pr
from app.poetry import service as svc
from app.poetry import timing as tm
from app.worker import process_one
from tests.test_library import png_bytes

ZABAR = "َ"
POEM = ("[verse]\nجب میں بکھرنے لگا\nخاک ہوا میں اڑی\n[chorus]\nشام کی لو جلی\nرات نے بات سنی\n"
        "[verse]\nدیا سا دل جلا\nہوا کا رخ مڑا\n[chorus]\nشام کی لو جلی\nرات نے بات سنی")


@pytest.fixture
def llm():
    calls = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
        title = schema.get("title")
        user = body["messages"][-1]["content"]
        calls.append({"schema": title, "user": user})
        if title == "_Draft":
            content = {"lyrics": POEM, "tags": "slow ghazal, harmonium, tabla"}
        elif title == "PoetryScenes":
            n = int(re.search(r"exactly (\d+) scenes", user)[1])
            content = {"character": "a woman in a dusty-rose hooded robe", "setting": "an old Mughal city",
                       "scenes": [{"image": f"A woman seen from behind in a sandstone lane, scene {i + 1}",
                                   "motion": "she walks slowly away; dust drifts"} for i in range(n)]}
        elif title == "Marked":
            rows = [re.sub(r"^\d+\.\s*", "", ln) for ln in user.split("\n")]
            content = {"lines": [r[:1] + ZABAR + r[1:] if r else r for r in rows]}
        else:
            content = {}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)},
                                                      "finish_reason": "stop"}], "usage": {}})

    set_transport(httpx.MockTransport(answer))
    yield calls
    set_transport(None)


def run_all():
    while process_one(driver_factory=lambda: MockDriver(step_seconds=0)):
        pass


def make(client, **over) -> dict:
    body = {"topic": "falling apart like dust", "language": "ur", "length_s": 30, "style": "slow ghazal", **over}
    r = client.post("/api/poetry", json=body)
    assert r.status_code == 201, r.text
    run_all()
    return client.get(f"/api/poetry/{r.json()['id']}").json()


def go_on(client, ps: dict) -> dict:
    r = client.post(f"/api/poetry/{ps['id']}/continue")
    assert r.status_code == 202, r.text
    run_all()
    return client.get(f"/api/poetry/{ps['id']}").json()


def stage(ps: dict, key: str) -> str:
    return next(s["status"] for s in ps["stages"] if s["key"] == key)


# ---------------------------------------------------------------- the pipeline

def test_pipeline_end_to_end_with_reviews(client, llm, monkeypatch):
    burns, finishes = [], []
    real_burn, real_finish = subtitles.burn, montage.finish
    monkeypatch.setattr(subtitles, "burn", lambda *a, **kw: burns.append(kw) or real_burn(*a, **kw))
    monkeypatch.setattr(montage, "finish", lambda *a, **kw: finishes.append(kw) or real_finish(*a, **kw))

    ps = make(client)
    assert ps["status"] == "review" and ps["review"] == "poem"
    assert len(ps["lines"]) == 8 and ps["lines"][2]["section"] == "chorus"
    # the poem got the aerab pass
    assert all(ZABAR in ln["text"] for ln in ps["lines"])
    assert ps["poem"]["tags"].startswith("slow ghazal")

    ps = go_on(client, ps)
    assert ps["review"] == "song" and ps["song"]["generation"]["status"] == "ready"
    with SessionLocal() as db:
        g = db.get(Generation, ps["song"]["generation"]["id"])
        assert g.kind == "song" and g.params["vocal"] == "female" and g.params["language"] == "ur"
        assert ZABAR in g.params["lyrics"] and db.get(MediaItem, ps["song"]["media_id"]).kind == "audio"
        assert db.get(MediaItem, ps["song"]["media_id"]).home == "poetry"

    ps = go_on(client, ps)
    assert ps["review"] == "timing"
    starts = [ln["start"] for ln in ps["lines"]]
    assert starts == sorted(starts) and ps["lines"][-1]["end"] <= ps["song"]["duration_s"] + 1e-6
    assert ps["timing"]["source"] in ("even", "phrases", "whisper")
    with SessionLocal() as db:
        lanes = {j.type: j.lane for j in db.query(Job).all()}
        assert lanes["poetry_timing"] == "audio" and lanes["poetry_short"] == "general"

    ps = go_on(client, ps)
    assert ps["review"] == "scenes"
    assert 4 <= len(ps["scenes"]) <= 8
    assert all(s["status"] == "done" and s["keyframe"] and s["clip"] for s in ps["scenes"])
    assert ps["scenes"][0]["start"] == 0 and ps["scenes"][-1]["end"] == pytest.approx(ps["song"]["duration_s"])
    clip = ps["scenes"][0]["clip"]
    assert clip["kind"] == "video" and clip["params"]["aspect_ratio"] == "9:16"
    assert clip["params"]["first_frame_id"] == ps["scenes"][0]["keyframe"]["id"]
    assert ps["scenes"][1]["keyframe"]["params"]["model"] == "krea2_turbo"

    ps = go_on(client, ps)
    assert ps["status"] == "done" and stage(ps, "render") == "done"
    out = ps["outputs"]
    assert out["video_url"] and out["preview_url"] and out["srt_url"] and not out["stale"]
    assert (out["width"], out["height"]) == (1080, 1920)
    assert [c["title"] for c in out["chapters"]] == ["Verse 1", "Chorus", "Verse 2", "Chorus"]
    # the shared assembly burned one fading cue per line
    assert len(finishes) == 1 and finishes[0]["fade"] > 0 and len(finishes[0]["cues"]) == 8
    assert burns and burns[0]["fade"] > 0 and burns[0]["language"] == "ur"
    with SessionLocal() as db:
        item = db.get(MediaItem, out["media_id"])
        assert item.kind == "video" and "poetry" in item.tags
        folder, _ = svc.files_dir(db.get(PoetryShort, ps["id"]))
        srt = (folder / out["srt_url"].rsplit("/", 1)[1]).read_text(encoding="utf-8")
        assert srt.count("-->") == 8
    assert client.get(out["video_url"]).status_code == 200

    # a timing edit only re-cuts
    rows = [{"start": ln["start"], "end": ln["end"]} for ln in ps["lines"]]
    rows[0]["end"] = round(rows[0]["end"] - 0.2, 3)
    r = client.patch(f"/api/poetry/{ps['id']}/timings", json={"lines": rows})
    assert r.status_code == 200, r.text
    assert r.json()["outputs"]["stale"] and r.json()["timing"]["edited"]
    gens_before = _gen_count()
    assert client.post(f"/api/poetry/{ps['id']}/render").status_code == 202
    run_all()
    ps = client.get(f"/api/poetry/{ps['id']}").json()
    assert ps["status"] == "done" and not ps["outputs"]["stale"]
    # one new Library version, no new GPU generations
    assert _gen_count() == gens_before + 1
    assert client.get("/api/poetry").json()[0]["id"] == ps["id"]


def _gen_count() -> int:
    with SessionLocal() as db:
        return db.query(Generation).count()


def test_one_click_runs_to_the_end_without_stopping(client, llm):
    ps = make(client, review=False, poem="first line here\nsecond line here\nthird line\nfourth line",
              language="en", aspect="16:9", title_card=True, title="Dust")
    assert ps["status"] == "done", ps["stages"]
    assert ps["poem"]["own"] and [ln["text"] for ln in ps["lines"]][0] == "first line here"
    assert ps["outputs"]["width"] == 1920
    # the title card goes before the song
    assert ps["outputs"]["duration_s"] == pytest.approx(ps["song"]["duration_s"] + svc.TITLE_S, abs=0.2)
    assert not any(c["schema"] == "_Draft" for c in llm)


def test_character_refs_and_regenerating_one_scene(client, llm):
    c = client.post("/api/characters", json={"name": "Noor", "description": "a woman in a rose hooded robe"}).json()
    folder = get_settings().data_dir / "workspaces"
    with SessionLocal() as db:
        ch = db.get(Character, c["id"])
        path = folder / ch.workspace_id / "panels" / "back.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes((400, 700)))
        back = Generation(workspace_id=ch.workspace_id, target_type="character", target_id=ch.id, kind="sheet_view",
                          status="ready", prompt="", params={"panel": "turn_back"}, seed=1, media_type="image/png",
                          file_path=path.relative_to(get_settings().data_dir).as_posix())
        db.add(back)
        db.commit()
        back_id = back.id
    ps = make(client, character_id=c["id"])
    for _ in range(3):
        ps = go_on(client, ps)
    assert ps["review"] == "scenes"
    kf = ps["scenes"][0]["keyframe"]
    # "seen from behind" picks the back turn of the sheet, on the edit model
    assert kf["params"]["reference_ids"] == [back_id] and kf["params"]["reference_labels"] == ["Noor"]
    assert kf["params"]["model"] == "qwen_image_edit_2511" and kf["prompt"].startswith("Picture 1 shows Noor")
    scene_prompts = next(x for x in llm if x["schema"] == "PoetryScenes")["user"]
    assert "rose hooded robe" in scene_prompts and "Scene 2:" in scene_prompts

    before = {s["id"]: (s["keyframe"]["id"], s["clip"]["id"]) for s in ps["scenes"]}
    target = ps["scenes"][1]["id"]
    r = client.post(f"/api/poetry/{ps['id']}/scenes/{target}/regenerate",
                    json={"what": "keyframe", "image_prompt": "A lantern in the rain"})
    assert r.status_code == 202, r.text
    run_all()
    ps = client.get(f"/api/poetry/{ps['id']}").json()
    after = {s["id"]: (s["keyframe"]["id"], s["clip"]["id"]) for s in ps["scenes"]}
    for sid, ids in before.items():
        if sid == target:
            assert ids[0] != after[sid][0] and ids[1] != after[sid][1]
        else:
            assert after[sid] == ids
    assert ps["scenes"][1]["image_prompt"] == "A lantern in the rain" and ps["review"] == "scenes"

    # a clip-only redo keeps the picture
    kf_id = after[target][0]
    client.post(f"/api/poetry/{ps['id']}/scenes/{target}/regenerate", json={"what": "clip"})
    run_all()
    ps = client.get(f"/api/poetry/{ps['id']}").json()
    sc = next(s for s in ps["scenes"] if s["id"] == target)
    assert sc["keyframe"]["id"] == kf_id and sc["clip"]["id"] != after[target][1]


def test_validation_and_edits(client, llm):
    assert client.post("/api/poetry", json={"language": "ur"}).status_code == 422
    assert client.post("/api/poetry", json={"topic": "x", "length_s": 301}).status_code == 422
    assert client.post("/api/poetry", json={"topic": "x", "character_id": "nope"}).status_code == 422
    assert client.post("/api/poetry", json={"topic": "x", "image_model": "ltx23_distilled"}).status_code == 422
    ps = make(client)
    r = client.patch(f"/api/poetry/{ps['id']}/poem", json={"lyrics": "[verse]\nایک\nدو\nتین\nچار", "title": "خاک"})
    assert r.status_code == 200 and len(r.json()["lines"]) == 4 and r.json()["title"] == "خاک"
    assert client.patch(f"/api/poetry/{ps['id']}/timings", json={"lines": [{"start": 0, "end": 1}]}).status_code == 409
    assert client.post(f"/api/poetry/{ps['id']}/render").status_code == 409
    ps = go_on(client, ps)
    ps = go_on(client, ps)
    assert ps["review"] == "timing"
    bad = [{"start": 2, "end": 3}, {"start": 1, "end": 2}, {"start": 4, "end": 5}, {"start": 6, "end": 7}]
    r = client.patch(f"/api/poetry/{ps['id']}/timings", json={"lines": bad})
    assert r.status_code == 422 and "Line 2" in r.json()["detail"]
    # a new song keeps the lines, times them again
    assert client.post(f"/api/poetry/{ps['id']}/song/regenerate").status_code == 202
    run_all()
    ps2 = client.get(f"/api/poetry/{ps['id']}").json()
    assert ps2["review"] == "song" and ps2["song"]["generation"]["id"] != ps["song"]["generation"]["id"]
    assert ps2["song"]["media_id"] == ps["song"]["media_id"]
    assert client.delete(f"/api/poetry/{ps['id']}").status_code == 204


# ---------------------------------------------------------------- the timing fit

def test_lines_fit_onto_phrases_in_order():
    regions = [(1.0, 3.0), (3.5, 5.0), (6.0, 9.0), (9.4, 12.0), (13.0, 15.0)]
    got = tm.fit(["aaaa", "bbbb", "cccc", "dddd"], regions, 16.0)
    assert got["source"] == "phrases"
    # five phrases, four lines: two short phrases merge, every line starts on a phrase
    assert [r["start"] for r in got["lines"]] == [1.0, 6.0, 9.4, 13.0]
    assert got["lines"][0]["end"] == 5.0


def test_more_lines_than_phrases_split_the_long_ones():
    regions = [(1.0, 3.0), (3.5, 5.0), (6.0, 9.0), (9.4, 12.0), (13.0, 15.0)]
    got = tm.fit(["a" * 4] * 8, regions, 16.0)
    assert got["source"] == "phrases" and len(got["lines"]) == 8
    starts = [r["start"] for r in got["lines"]]
    assert starts == sorted(starts) and starts[0] == 1.0 and got["lines"][-1]["end"] == 15.0
    assert all(r["end"] > r["start"] for r in got["lines"])


def test_weights_follow_line_length():
    got = tm.fit(["a" * 30, "b" * 10], [(0.0, 4.0), (4.5, 6.0), (6.5, 8.0)], 8.0)
    # the long line takes the first two phrases' worth of time, the short one the last
    assert got["lines"][1]["start"] == 6.5


def test_even_spread_when_there_is_no_pause():
    got = tm.fit(["one", "two", "three"], [(0.0, 9.0)], 10.0)
    assert got["source"] == "even"
    assert [r["start"] for r in got["lines"]] == [0.0, 3.0, 6.0]
    # nothing heard at all: the whole song
    assert tm.fit(["a", "b"], [], 8.0)["lines"] == [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 8.0}]


def test_whisper_words_win_when_they_match_the_poem():
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": w}
             for i, w in enumerate("one two three four five six".split())]
    got = tm.fit(["one two", "three four", "five six"], [(0, 4)], 4.0, words)
    assert got["source"] == "whisper" and got["lines"][1] == {"start": 1.0, "end": 1.9}
    # aerab on the written line doesn't stop a match
    marked = tm.fit(["دِل", "رات"], [], 4.0, [{"start": 0.2, "end": 0.8, "word": "دل"},
                                              {"start": 1.5, "end": 2.0, "word": "رات"}])
    assert marked["source"] == "whisper"
    # a transcript of something else is ignored
    other = [{"start": i, "end": i + 0.5, "word": w} for i, w in enumerate("mock line of the transcript".split())]
    assert tm.fit(["one two", "three four"], [(0, 4)], 4.0, other)["source"] == "even"


def test_voiced_regions_find_the_phrases():
    sr = tm.RATE
    x = np.zeros(sr * 6, dtype=np.float32)
    t = np.arange(sr, dtype=np.float32)
    x[sr:2 * sr] = 0.3 * np.sin(t * 0.1)
    x[3 * sr:5 * sr] = 0.3 * np.sin(np.arange(2 * sr, dtype=np.float32) * 0.1)
    assert tm.voiced_regions(x) == [(1.0, 2.0), (3.0, 5.0)]
    assert tm.voiced_regions(np.zeros(sr, dtype=np.float32)) == []


def test_timing_edits_are_checked():
    assert tm.check_edit([{"start": 0, "end": 1}, {"start": 1, "end": 2}], 3.0)[1] == {"start": 1.0, "end": 2.0}
    for rows, needle in (([{"start": 1, "end": 1}], "ends before"), ([{"start": 0, "end": 2}, {"start": 1, "end": 3}],
                         "starts before"), ([{"start": 0, "end": 9}], "after the song")):
        with pytest.raises(ValueError, match=needle):
            tm.check_edit(rows, 5.0)


def test_scenes_group_on_line_starts():
    starts = [i * 2.5 for i in range(24)]
    firsts = svc.group_scenes(starts, 60.0)
    assert len(firsts) == 7 and firsts[0] == 0 and firsts == sorted(set(firsts))
    assert svc.group_scenes(starts[:3], 60.0) == [0, 1, 2]
    assert len(svc.group_scenes(starts[:10], 25.0)) == svc.MIN_SCENES


def test_chapters_and_cues_follow_the_sections():
    ps = PoetryShort(lines=[{"text": f"l{i}", "section": s, "part": p, "start": i * 2.0, "end": i * 2.0 + 1.5}
                            for i, (s, p) in enumerate([("verse", "verse:1"), ("verse", "verse:1"),
                                                        ("chorus", "chorus:1"), ("verse", "verse:2")])])
    plan = pr.plan_for(ps, 10.0, 3.0)
    assert [c["title"] for c in plan["chapters"]] == ["Verse 1", "Chorus", "Verse 2"]
    assert plan["chapters"][0]["start"] == 0 and plan["chapters"][1]["start"] == 7.0
    assert plan["cues"][0] == {"start": 3.0, "end": 4.5, "text": "l0"} and plan["total"] == 13.0


def test_the_song_is_long_enough_to_sing_the_whole_poem():
    from app.poetry import stages as pst

    # eight ghazal lines don't fit in 60 s; the song model would stop mid-poem and the subtitles run ahead
    lines = ["شمس ڈوبی ہے، شام بھی آئی ہے"] * 8
    secs = pst.song_seconds(lines, "slow ghazal, harmonium", 60, 240)
    assert 80 <= secs <= pst.SONG_MAX_S
    assert pst.song_seconds(lines, "slow ghazal", 60, 70) == 70  # never over the model's limit
    assert pst.song_seconds(["la la"], "upbeat pop", 45) == 45  # a short poem keeps the asked length
    assert pst.sections_for(60) == ["verse", "chorus"]


def test_a_truncated_keyframe_is_not_readable(tmp_path, monkeypatch):
    from app.poetry import stages as pst

    good, bad = tmp_path / "good.png", tmp_path / "bad.png"
    good.write_bytes(png_bytes())
    bad.write_bytes(png_bytes()[:40])
    monkeypatch.setattr(pst, "generation_file", lambda g: g)
    assert pst.readable(good) and not pst.readable(bad) and not pst.readable(tmp_path / "missing.png")
