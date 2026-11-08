"""Remix a song (contract v43): the effect graphs, tempo and key, and every recipe through the mock driver."""
import pytest

from app import audio_analysis as aa
from app import remix
from app import remix_analysis as ra
from app.config import get_settings
from app.db import SessionLocal
from app.models import Generation, Job, MediaItem
from tests.test_audio_edit import lavfi, tone, upload_audio
from tests.test_song_voice import sing_on  # noqa: F401 - fixture
from tests.test_storyboard import _run_all


def clicks(path, bpm: float, seconds: float = 12.0, freq: int = 1000):
    beat = 60.0 / bpm
    return lavfi(path, f"aevalsrc=0.5*lt(mod(t\\,{beat:.6f})\\,0.03)*sin(2*PI*{freq}*t):s=48000:d={seconds}")


def chord(path, freqs, seconds: float = 6.0):
    wave = "+".join(f"0.1*sin(2*PI*{f}*t)" for f in freqs)
    return lavfi(path, f"aevalsrc={wave}:s=48000:d={seconds}")


def make(client, sources, **body):
    return client.post("/api/audio/remix", json={"sources": sources, **body})


def gen_of(item_id) -> Generation:
    with SessionLocal() as db:
        return db.get(Generation, db.get(MediaItem, item_id).generation_id)


# ------------------------------------------------------------------ on paper

def test_effect_graphs():
    assert "asetrate=38400" in remix.fx_graph("slowed_reverb", None, 10)
    hall = remix.fx_graph("slowed_reverb", None, 10, ir=True)
    assert "[1:a]" in hall and "afir=" in hall and "aecho" not in hall
    assert "asetrate=60000" in remix.fx_graph("nightcore", None, 10)
    assert remix.fx_graph("lofi", None, 10).count("vibrato=") == 2
    assert "anoisesrc" in remix.fx_graph("lofi", None, 10) and "amix" in remix.fx_graph("lofi", None, 10)
    assert "apulsator" in remix.fx_graph("eight_d", None, 10)
    assert remix.fx_graph("bass_boost", None, 10).endswith("[out]")
    # key and tempo: rubberband when the build has it, resample + atempo otherwise
    assert "rubberband=tempo=1.100000:pitch=1.122462:pitchq=quality:channels=together" in remix.fx_graph("key_tempo", {"semitones": 2, "tempo": 1.1}, 10, True)
    plain = remix.fx_graph("key_tempo", {"semitones": 12, "tempo": 1.0}, 10)
    assert "asetrate=96000" in plain and "atempo=0.5" in plain
    assert remix.pitch_tempo(0, 1.0, False) == []
    # the music ducks under the voice and leaves a little room around 2.5 kHz
    g = remix.over_graph(30.0, ["atempo=1.1"], 0.25)
    assert "sidechaincompress" in g and "equalizer=f=2500" in g and "adelay=250|250" in g and "atrim=duration=30.5" in g


def test_tempo_and_key_maths():
    assert ra.tempo_ratio(120, 100) == pytest.approx(1.2)
    # double time is nearer than a 50 % stretch
    assert ra.tempo_ratio(120, 240) == pytest.approx(1.0)
    assert ra.tempo_ratio(120, 62) == pytest.approx(120 / 124, abs=1e-3)
    assert ra.tempo_ratio(None, 100) == 1.0 and ra.tempo_ratio(120, 50, 0.9, 1.1) == 1.0
    assert ra.semitones_between("D major", "C major") == 2
    assert ra.semitones_between("C major", "A minor") == 0
    assert ra.semitones_between("C major", "G major") == 5
    assert ra.semitones_between("G major", "C major") == -5
    assert ra.semitones_between(None, "C major") == 0
    # harmonic: a neighbour on the Camelot wheel is good enough, so the smaller move wins
    assert ra.semitones_between("C major", "G major", harmonic=True) == 0
    assert ra.semitones_between("C major", "D major", harmonic=True) == -2
    # F# up to G or down to F: both one step from C on the wheel
    assert abs(ra.semitones_between("C major", "F# major", harmonic=True)) == 1
    assert [ra.camelot(k) for k in ("C major", "A minor", "G major", "E minor", "B major", None)] == [
        "8B", "8A", "9B", "9A", "1B", None]


def test_hears_the_tempo_and_key(tmp_path):
    got = ra.analyse(clicks(tmp_path / "c.wav", 120))
    assert got["bpm"] == pytest.approx(120, abs=3) and got["duration_s"] == pytest.approx(12, abs=0.1)
    assert ra.analyse(clicks(tmp_path / "d.wav", 96))["bpm"] == pytest.approx(96, abs=3)
    # C, E, G with a B and D passing: C major
    assert ra.analyse(chord(tmp_path / "k.wav", (261.63, 329.63, 392.0, 130.81)))["key"] == "C major"
    # cached beside the file
    assert (tmp_path / "c.remix.json").is_file()


@pytest.mark.parametrize("recipe", remix.FX)
def test_every_effect_renders(tmp_path, recipe):
    src = tone(tmp_path / "s.wav", 3.0, 330)
    dst = tmp_path / f"{recipe}.flac"
    remix.render_fx(recipe, {"semitones": -2, "tempo": 1.2} if recipe == "key_tempo" else None, src, dst)
    info = aa.stream_info(dst)
    expect = {"slowed_reverb": 3 / 0.85, "sped_up": 3 / 1.15, "nightcore": 3 / 1.25, "lofi": 3 / 0.95,
              "key_tempo": 3 / 1.2}.get(recipe, 3.0)
    assert info.has_audio and info.sample_rate == 48000 and info.duration == pytest.approx(expect, abs=0.3)


def test_auto_plans():
    avail = {k: None for k in remix.RECIPES}
    one = [s["recipe"] for s in remix.auto_plan(1, avail)]
    assert one == ["slowed_reverb", "sped_up", "lofi", "new_beat"]
    assert remix.auto_plan(1, {**avail, "new_beat": "no"})[-1]["recipe"] == "eight_d"
    assert [s["recipe"] for s in remix.auto_plan(2, avail)] == ["mashup", "mashup", "medley", "medley"]


# ------------------------------------------------------------------ the API and the jobs

def test_auto_remix_of_one_song(client, tmp_path, fast_driver):
    a = upload_audio(client, clicks(tmp_path / "song.wav", 110, 8.0), title="Harbour")
    rec = client.get("/api/audio/remix/recipes").json()
    by = {r["id"]: r for r in rec["recipes"]}
    assert by["mashup"]["min_sources"] == 2 and by["new_beat"]["available"] is True
    # Seed-VC isn't installed in tests; the reason says so without any licence talk
    assert by["new_voice"]["available"] is False and "set up" in by["new_voice"]["reason"]

    got = client.get(f"/api/audio/remix/analyse/{a['id']}").json()
    assert got["bpm"] == pytest.approx(110, abs=4)

    r = make(client, [a["id"]], auto=True)
    assert r.status_code == 202, r.text
    out = r.json()
    titles = [i["title"] for i in out["items"]]
    assert titles[:3] == ["Harbour — Slowed + reverb", "Harbour — Sped up", "Harbour — Lo-fi"]
    assert titles[3].startswith("Harbour — New beat (")
    assert [(j["type"], j["lane"]) for j in out["jobs"]] == [("audio_remix_fx", "general")] * 3 + [("audio_remix", "audio")]

    listed = client.get(f"/api/audio/remix/{a['id']}").json()
    assert len(listed["items"]) == 4 and len(listed["jobs"]) == 4
    _run_all(fast_driver)
    with SessionLocal() as db:
        for j in out["jobs"]:
            job = db.get(Job, j["id"])
            assert job.status == "done", job.error
    for item in out["items"]:
        g = gen_of(item["id"])
        assert g.status == "ready" and g.media_type == "audio/flac" and g.params["kind"] == "remix"
        assert aa.stream_info(get_settings().data_dir / g.file_path).has_audio
    beat = gen_of(out["items"][3]["id"])
    assert beat.params["model"].startswith("ace15")
    with SessionLocal() as db:
        res = db.get(Job, out["jobs"][3]["id"]).result
    assert res["bpm"] == pytest.approx(110, abs=4) and "offset_s" in res
    # the split is kept for the next version of the same song
    assert (get_settings().data_dir / "workspaces").rglob("remix/stems/*/vocals.flac")
    listed = client.get(f"/api/audio/remix/{a['id']}").json()
    assert listed["jobs"] == [] and all(i["media_url"] for i in listed["items"])


def test_mashup_and_medley_of_two_songs(client, tmp_path, fast_driver):
    a = upload_audio(client, clicks(tmp_path / "a.wav", 100, 10.0), title="Alpha")
    b = upload_audio(client, clicks(tmp_path / "b.wav", 120, 10.0, 600), title="Beta")
    r = make(client, [a["id"], b["id"]], recipes=[
        {"recipe": "mashup", "options": {"vocals_from": 1, "music_from": 0}},
        {"recipe": "medley", "options": {"then": "slowed_reverb"}}])
    assert r.status_code == 202, r.text
    items = r.json()["items"]
    assert [i["title"] for i in items] == ["Beta × Alpha — Mashup", "Alpha + Beta — Medley, slowed + reverb"]
    _run_all(fast_driver)
    mash, med = gen_of(items[0]["id"]), gen_of(items[1]["id"])
    assert mash.status == "ready" and med.status == "ready"
    # the mashup is as long as the vocals it carries
    assert aa.stream_info(get_settings().data_dir / mash.file_path).duration == pytest.approx(10.0, abs=0.3)
    with SessionLocal() as db:
        res = db.get(Job, mash.job_id).result
    assert res["tempo"] == pytest.approx(1.2, abs=0.08)
    # both songs list it
    assert len(client.get(f"/api/audio/remix/{b['id']}").json()["items"]) == 2


def test_a_new_voice(client, tmp_path, sing_on, fast_driver):  # noqa: F811
    a = upload_audio(client, tone(tmp_path / "s.wav", 3.0, 330), title="Lullaby")
    r = make(client, [a["id"]], recipes=[{"recipe": "new_voice", "options": {"vocal": "choir"}}])
    assert r.status_code == 202, r.text
    item = r.json()["items"][0]
    assert item["title"] == "Lullaby — Sung by a children's choir"
    g = gen_of(item["id"])
    assert len(g.params["sing_as"]["targets"]) == 3
    _run_all(fast_driver)
    assert gen_of(item["id"]).status == "ready"


def test_what_is_refused(client, tmp_path):
    a = upload_audio(client, tone(tmp_path / "a.wav", 2.0), title="A")
    b = upload_audio(client, tone(tmp_path / "b.wav", 2.0, 300), title="B")
    cases = [
        ([a["id"]], {"recipes": [{"recipe": "mashup"}]}, "2 songs"),
        ([a["id"], b["id"]], {"recipes": [{"recipe": "lofi"}]}, "one song"),
        ([a["id"], b["id"]], {"recipes": [{"recipe": "mashup", "options": {"vocals_from": 1, "music_from": 1}}]},
         "two different"),
        ([a["id"]], {"recipes": [{"recipe": "new_voice", "options": {"vocal": "boy"}}]}, "set up"),
        ([a["id"]], {"recipes": [{"recipe": "key_tempo"}]}, "move the key"),
        ([a["id"]], {"recipes": [{"recipe": "dubstep"}]}, "Unknown"),
        ([a["id"]], {"recipes": []}, "at least one"),
        ([a["id"], a["id"]], {"auto": True}, "twice"),
    ]
    for sources, body, why in cases:
        r = make(client, sources, **body)
        assert r.status_code == 422 and why in r.json()["detail"], (body, r.text)
    assert make(client, [a["id"]] * 4, auto=True).status_code == 422
    assert make(client, ["nope"], auto=True).status_code == 404
