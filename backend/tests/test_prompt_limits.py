"""Prompts have no practical length limit (owner, 7 Nov): a long one is taken whole, not refused or cut."""
from sqlalchemy import select

from app.db import SessionLocal
from app.limits import PROMPT_MAX
from app.models import Generation

LONG = ("a quiet harbour at dawn, fishing boats, gulls, soft mist over the water, " * 280)[:20_000]


def _prompt_of(kind: str) -> str:
    with SessionLocal() as db:
        return db.scalars(select(Generation.prompt).where(Generation.kind == kind)
                          .order_by(Generation.created_at.desc())).first()


def test_a_long_prompt_is_taken_whole(client):
    assert len(LONG) == 20_000 < PROMPT_MAX
    r = client.post("/api/images/generate", json={"prompt": LONG})
    assert r.status_code == 202, r.text
    assert _prompt_of("image") == LONG

    r = client.post("/api/videos/generate", json={"prompt": LONG})
    assert r.status_code == 202, r.text

    r = client.post("/api/audio/generate", json={"kind": "music", "prompt": LONG})
    assert r.status_code == 202, r.text

    r = client.post("/api/prompts/enhance", json={"prompt": LONG, "kind": "image", "mode": "on"})
    assert r.status_code != 422, r.text


def test_a_runaway_paste_is_still_stopped(client):
    r = client.post("/api/images/generate", json={"prompt": "x" * (PROMPT_MAX + 1)})
    assert r.status_code == 422
