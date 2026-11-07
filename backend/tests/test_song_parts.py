"""A lead child and a group of children (owner, 7 Nov): roles from the lyrics, the masks, the full song."""
import numpy as np
import pytest

from app import song_parts as sp
from app.config import get_settings
from app.db import SessionLocal
from app.models import Generation, MediaItem
from tests.test_storyboard import _run_all

LYRICS = ("[intro]\n(Ooh, ooh)\n[verse: lead]\nI tidy my room before the sun is high (all done!)\n"
          "I water the plants on the windowsill\n[chorus: group]\nThank you, thank you, every day\n"
          "[verse]\nWe share our lunch beneath the tree\n[chorus]\nThank you, thank you, every day")


def test_roles_from_markers_brackets_and_defaults():
    clean, lines = sp.parse(LYRICS)
    # the model gets plain markers; the words in brackets stay so they are sung
    assert "[verse]" in clean and "[verse: lead]" not in clean and "(all done!)" in clean
    roles = [(ln["section"], ln["role"]) for ln in lines]
    assert roles == [("intro", "group"), ("verse", "lead"), ("verse", "lead"), ("chorus", "group"),
                     ("verse", "lead"), ("chorus", "all")]
    lead_line = lines[1]
    assert lead_line["sung"] == "I tidy my room before the sun is high all done!"
    (a, b), = lead_line["answers"]
    assert 0.8 < a < b <= 1.0
    assert sp.role_of("Everyone", "verse") == "all" and sp.role_of("kids", "verse") == "group"
    assert sp.role_of(None, "bridge") == "lead" and sp.role_of("nonsense", "outro") == "all"


def test_spans_give_answers_to_the_group():
    lines = [{"role": "lead", "answers": [(0.75, 1.0)], "start": 10.0, "end": 14.0},
             {"role": "all", "answers": [], "start": 15.0, "end": 18.0},
             {"role": "group", "answers": [], "start": 20.0, "end": 22.0}]
    lead, group = sp.spans(lines, 30.0)
    assert lead == [(9.92, 14.08), (14.92, 18.08)]
    assert group[0][0] == pytest.approx(13.04, abs=0.01) and (19.92, 22.08) in group


def test_envelope_fades_instead_of_clicking():
    env = sp.envelope(44100, [(0.25, 0.75)], rate=44100, fade=0.04)
    assert env[0] == 0 and env[22050] == pytest.approx(1.0) and env[-1] == 0
    edge = env[int(0.23 * 44100):int(0.27 * 44100)]
    assert 0.2 < edge[len(edge) // 2] < 0.8 and np.all(np.diff(edge) >= -1e-6)


@pytest.fixture
def sing_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "song_voice_engine", "seed-vc")


def test_a_lead_child_and_a_group_sing_it(client, sing_on, fast_driver):
    r = client.post("/api/audio/generate", json={"kind": "song", "prompt": "children's nasheed, soft drum",
                                                 "lyrics": LYRICS, "vocal": "lead_group", "lead_voice": "boy",
                                                 "group_size": 5, "duration_s": 12})
    assert r.status_code == 202, r.text
    item = r.json()["items"][0]
    with SessionLocal() as db:
        g = db.get(Generation, db.get(MediaItem, item["id"]).generation_id)
        p = g.params
    assert p["vocal"] == "female" and "[verse: lead]" not in p["lyrics"] and "[verse]" in p["lyrics"]
    assert p["sing_as"]["lead_name"] == "a boy" and len(p["sing_as"]["targets"]) == 5
    _run_all(fast_driver)
    with SessionLocal() as db:
        g = db.get(Generation, db.get(MediaItem, item["id"]).generation_id)
        assert g.status == "ready", g.params
        assert g.params["sung_as"] == "a boy with a children's group"
        parts = g.params["sung_parts"]
        assert parts.get("lead_s", 0) > 0 or parts.get("fallback") == "choir"


def test_lead_group_needs_the_voice_when_its_a_library_voice(client, sing_on):
    r = client.post("/api/audio/generate", json={"kind": "song", "prompt": "nasheed", "lyrics": LYRICS,
                                                 "vocal": "lead_group", "lead_voice": "voice", "duration_s": 12})
    assert r.status_code == 422 and "Pick the voice" in r.json()["detail"]
