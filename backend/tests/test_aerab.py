"""Aerab for Urdu lyrics and dialogue: marks only ever land on the writer's own letters."""
import json

import httpx
import pytest

from app import aerab
from app.db import SessionLocal
from app.llm import set_transport
from app.models import Generation
from tests.test_storyboard import _run_all

PLAIN = "دل کی بات"
MARKED = "دِل کی بات"


class Marker:
    """Fake LLM: answers a marking request by running `fn` over the numbered lines it was sent."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][-1]["content"]
        if not isinstance(user, str):
            return httpx.Response(500)  # the picture check on a finished frame, not a marking request
        self.calls += 1
        lines = [row.split(". ", 1)[1] for row in user.split("\n")]
        reply = {"lines": [self.fn(x) for x in lines]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)},
                                                      "finish_reason": "stop"}], "usage": {}})


@pytest.fixture
def llm():
    def install(fn):
        fake = Marker(fn)
        set_transport(httpx.MockTransport(fake))
        return fake
    yield install
    set_transport(None)


def test_transfer_keeps_the_writers_letters():
    assert aerab.transfer(PLAIN, MARKED) == MARKED
    # an Arabic ye / kaf swapped in by the LLM still lands on the Urdu letters
    assert aerab.transfer(PLAIN, "دِل كي بات") == MARKED
    # one changed letter (کی -> کا) no longer sinks the line: its neighbours keep their marks
    assert aerab.transfer(PLAIN, "دِل کا بات") == MARKED
    # a rewritten line is still refused
    assert aerab.transfer(PLAIN, "دِل میں اُمید") is None
    # marks the writer typed win
    assert aerab.transfer("دُل کی بات", MARKED) == "دُل کی بات"
    assert aerab.strip(MARKED) == PLAIN


def test_merge_skips_markers_english_and_bad_lines():
    text = f"[verse]\n{PLAIN}\nla la\n{PLAIN} ہے"
    out, marked, kept = aerab.merge(text, [MARKED, "بالکل اور"])
    assert out == f"[verse]\n{MARKED}\nla la\n{PLAIN} ہے"
    assert (marked, kept) == (1, 1)


def test_unmarked_leaves_marked_lines_alone():
    assert aerab.unmarked(PLAIN) and not aerab.unmarked("دِلْ کِی بَات") and not aerab.unmarked("hello")
    assert aerab.quoted_spoken(f'A man says "{PLAIN}" softly, rain. Sign reads "OPEN"') == [PLAIN]


def test_add_aerab_endpoint(client, llm):
    fake = llm(lambda x: x.replace("دل", "دِل"))
    r = client.post("/api/audio/lyrics/aerab", json={"lyrics": f"[chorus]\n{PLAIN}\n{PLAIN}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"lyrics": f"[chorus]\n{MARKED}\n{MARKED}", "marked": 2, "kept": 0, "low_coverage": 0,
                        "language": "ur", "complete": True, "remaining_chars": 0}
    assert fake.calls == 1
    assert client.post("/api/audio/lyrics/aerab", json={"lyrics": "[verse]\nhello"}).status_code == 422


def test_add_aerab_offline_is_503(client):
    r = client.post("/api/audio/lyrics/aerab", json={"lyrics": PLAIN})
    assert r.status_code == 503


def test_urdu_song_is_marked_before_it_renders(client, llm, fast_driver):
    llm(lambda x: x.replace("دل", "دِل"))
    r = client.post("/api/audio/generate", json={"kind": "song", "prompt": "ghazal, harmonium", "language": "ur",
                                                  "lyrics": f"[verse]\n{PLAIN}", "duration_s": 2})
    assert r.status_code == 202, r.text
    _run_all(fast_driver)
    with SessionLocal() as db:
        g = db.get(Generation, r.json()["items"][0]["generation_id"])
        assert g.status == "ready"
        assert g.params["lyrics"] == f"[verse]\n{MARKED}" and g.params["lyrics_written"] == f"[verse]\n{PLAIN}"


def test_aerab_off_or_llm_away_still_renders(client, fast_driver):
    r = client.post("/api/audio/generate", json={"kind": "song", "prompt": "ghazal", "language": "ur",
                                                  "lyrics": PLAIN, "duration_s": 2})
    _run_all(fast_driver)
    with SessionLocal() as db:
        g = db.get(Generation, r.json()["items"][0]["generation_id"])
        assert g.status == "ready" and g.params["lyrics"] == PLAIN and "lyrics_written" not in g.params


def test_quoted_urdu_dialogue_in_a_video_prompt(client, llm, fast_driver):
    fake = llm(lambda x: x.replace("دل", "دِل"))
    r = client.post("/api/videos/generate", json={"prompt": f'An old man by the window whispers "{PLAIN}", rain outside',
                                                   "duration_s": 2})
    assert r.status_code == 202, r.text
    _run_all(fast_driver)
    with SessionLocal() as db:
        g = db.get(Generation, r.json()["generation_id"])
        assert g.status == "ready"
        assert f'"{MARKED}"' in g.prompt and g.params["prompt_written"].count(PLAIN) == 1
    assert fake.calls == 1


def test_video_without_urdu_never_calls_the_llm(client, llm, fast_driver):
    fake = llm(lambda x: x)
    client.post("/api/videos/generate", json={"prompt": 'A woman says "hello" at dawn', "duration_s": 2})
    _run_all(fast_driver)
    assert fake.calls == 0
