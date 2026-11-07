"""Poetry Short Episodes (7 Nov): up to 240 s, a Library voice, an intro and outro card, more scenes."""
import pytest

from app.config import get_settings
from app.poetry import service as svc
from app.poetry import stages as pst
from tests.test_library import png_bytes
from tests.test_poetry import llm, make  # noqa: F401  (llm is a fixture)


def _upload_picture(client, name="intro.png") -> str:
    r = client.post("/api/media/upload", files={"file": (name, png_bytes(), "image/png")})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    return (body.get("item") or body.get("items", [body])[0])["id"]


def test_length_goes_to_240_and_no_further(client, llm):  # noqa: F811
    assert client.post("/api/poetry", json={"topic": "the moon", "length_s": 240}).status_code == 201
    assert client.post("/api/poetry", json={"topic": "the moon", "length_s": 241}).status_code == 422


def test_a_four_minute_episode_has_more_scenes_and_sections():
    starts = [i * 4.0 for i in range(60)]
    assert len(svc.group_scenes(starts, 240.0)) == 27
    assert len(svc.group_scenes(starts[:20], 60.0)) == 7  # a one-minute Short is unchanged
    assert pst.sections_for(240) == ["verse", "chorus", "verse", "chorus", "verse", "bridge", "chorus"]
    assert pst.SONG_MAX_S == 240.0
    # a poem too long for 4 minutes still stops at the model's limit
    assert pst.song_seconds(["a long line of the nasheed"] * 60, "children's nasheed", 230, 240) == 240


def test_a_library_voice_sings_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "song_voice_engine", "seed-vc")
    r = client.post("/api/poetry", json={"topic": "the moon", "vocal": "voice"})
    assert r.status_code == 422 and "Pick the voice" in r.json()["detail"]
    v = client.post("/api/voices", json={"kind": "design", "name": "Hamza", "language": "ur",
                                         "design": {"description": "a bright, clear ten-year-old boy"}})
    assert v.status_code in (200, 201), v.text
    r = client.post("/api/poetry", json={"topic": "the moon", "vocal": "voice", "sing_voice_id": v.json()["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["settings"]["sing_voice_id"] == v.json()["id"]


def test_bookends_are_checked(client, tmp_path):
    r = client.post("/api/poetry", json={"topic": "the moon", "intro_media_id": "nope"})
    assert r.status_code == 422 and "intro" in r.json()["detail"]


def test_intro_and_outro_frame_the_song_without_captions(client, llm):  # noqa: F811
    intro, outro = _upload_picture(client, "intro.png"), _upload_picture(client, "outro.png")
    ps = make(client, review=False, poem="first line here\nsecond line here\nthird line\nfourth line",
              language="en", aspect="16:9", intro_media_id=intro, outro_media_id=outro)
    assert ps["status"] == "done", ps["stages"]
    song = ps["song"]["duration_s"]
    # the intro and outro pictures hold BOOKEND_S each, around the song
    assert ps["outputs"]["duration_s"] == pytest.approx(song + 2 * svc.BOOKEND_S, abs=0.3)


def test_plan_pads_the_outro_and_delays_the_captions():
    from app.models import PoetryShort
    from app.poetry import render as pr

    ps = PoetryShort(lines=[{"text": "l0", "section": "verse", "part": "verse:1", "start": 0.5, "end": 2.0}])
    plan = pr.plan_for(ps, 10.0, 4.0, 4.0)
    assert plan["cues"][0]["start"] == 4.5 and plan["total"] == 18.0 and plan["audio_len"] == 10.0
