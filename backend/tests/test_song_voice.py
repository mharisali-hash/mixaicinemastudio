"""Songs sung by a child, a choir or a Library voice (owner, 7 Nov), and lyrics from a split song."""
import pytest

from app import audio_analysis as aa
from app import song_voice
from app.config import get_settings
from app.db import SessionLocal
from app.models import Generation, Job, MediaItem
from tests.test_audio_edit import tone, upload_audio
from tests.test_storyboard import _run_all

LYRICS = "[verse]\nLittle stars above the town\n[chorus]\nShine on, shine on"


@pytest.fixture
def sing_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "song_voice_engine", "seed-vc")


def _song(client, **body):
    return client.post("/api/audio/generate", json={"kind": "song", "prompt": "gentle lullaby, piano",
                                                     "lyrics": LYRICS, "duration_s": 3, **body})


def _gen(item_id) -> Generation:
    with SessionLocal() as db:
        return db.get(Generation, db.get(MediaItem, item_id).generation_id)


def test_off_until_the_engine_is_installed(client):
    assert client.get("/api/audio/song-voices").json()["available"] is False
    r = _song(client, vocal="boy")
    assert r.status_code == 422 and "set up" in r.json()["detail"]
    # the stock vocals are untouched
    assert _song(client, vocal="female").status_code == 202


def test_a_boy_sings_it(client, sing_on, fast_driver):
    assert client.get("/api/audio/song-voices").json() == {"available": True,
                                                           "vocals": ["boy", "girl", "choir", "voice", "lead_group"]}
    r = _song(client, vocal="boy")
    assert r.status_code == 202, r.text
    item = r.json()["items"][0]
    p = _gen(item["id"]).params
    # the model sings with a stock female vocal; the worker changes it to the boy afterwards
    assert p["vocal"] == "female" and p["sing_as"]["vocal"] == "boy"
    assert [t["voice"]["kind"] for t in p["sing_as"]["targets"]] == ["design"]
    _run_all(fast_driver)
    g = _gen(item["id"])
    assert g.status == "ready", g.error if hasattr(g, "error") else None
    assert g.params["sung_as"] == "a boy"
    info = aa.stream_info(get_settings().data_dir / g.file_path)
    assert info.has_audio and info.duration == pytest.approx(3.0, abs=0.2)
    # the boy's reference clip is spoken once and kept for the next song
    assert len(list((get_settings().data_dir / "song_voices").glob("*.flac"))) == 1


def test_a_choir_is_three_children(client, sing_on, fast_driver):
    r = _song(client, vocal="choir")
    assert r.status_code == 202, r.text
    targets = _gen(r.json()["items"][0]["id"]).params["sing_as"]["targets"]
    assert len(targets) == 3 and len({t["delay"] for t in targets}) == 3
    _run_all(fast_driver)
    g = _gen(r.json()["items"][0]["id"])
    assert g.status == "ready" and g.params["sung_as"] == "a children's choir"


def test_a_library_voice(client, sing_on):
    r = _song(client, vocal="voice")
    assert r.status_code == 422 and "Pick the voice" in r.json()["detail"]
    r = _song(client, vocal="voice", sing_voice_id="nope")
    assert r.status_code == 422
    v = client.post("/api/voices", json={"kind": "design", "name": "Uncle Rafiq", "language": "ur",
                                         "gender_hint": "male",
                                         "design": {"description": "a warm, deep middle-aged man's voice"}})
    assert v.status_code in (200, 201), v.text
    r = _song(client, vocal="voice", sing_voice_id=v.json()["id"])
    assert r.status_code == 202, r.text
    p = _gen(r.json()["items"][0]["id"]).params
    assert p["vocal"] == "male" and p["sing_as"]["name"] == "Uncle Rafiq"


def test_no_lyrics_no_voice(client, sing_on):
    r = client.post("/api/audio/generate", json={"kind": "song", "prompt": "lullaby", "vocal": "girl",
                                                 "duration_s": 3})
    assert r.status_code == 422 and "lyrics" in r.json()["detail"]


def test_mix_graph():
    one = song_voice.mix_graph([{"delay": 0, "pan": 0}])
    assert "adelay" not in one and "volume=1.0" in one and "amix=inputs=2" in one
    three = song_voice.mix_graph([{"delay": 0, "pan": -0.45}, {"delay": 0.022, "pan": 0.45},
                                  {"delay": 0.037, "pan": 0}])
    assert "adelay=22|22" in three and "balance_out=-0.45" in three and "amix=inputs=4" in three


def test_poetry_children_need_the_engine(client):
    r = client.post("/api/poetry", json={"topic": "the moon", "vocal": "girl"})
    assert r.status_code == 422


def test_split_writes_the_lyrics(client, tmp_path, fast_driver):
    a = upload_audio(client, tone(tmp_path / "song.wav", 3.0, 330), title="Lori")
    r = client.post(f"/api/audio/{a['id']}/stems", json={"lyrics": True})
    assert r.status_code == 202, r.text
    _run_all(fast_driver)
    with SessionLocal() as db:
        assert db.get(Job, r.json()["job"]["id"]).status == "done"
    splits = client.get(f"/api/audio/{a['id']}/stems").json()["splits"]
    assert "mock transcript" in (splits[0]["lyrics"] or "")
    # without the option there are no lyrics
    r = client.post(f"/api/audio/{a['id']}/stems", json={"mode": "four"})
    _run_all(fast_driver)
    four = [s for s in client.get(f"/api/audio/{a['id']}/stems").json()["splits"] if s["mode"] == "four"][0]
    assert four["lyrics"] is None
