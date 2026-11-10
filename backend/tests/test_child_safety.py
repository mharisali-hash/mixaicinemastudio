"""A child in a sexual or nude way is refused for every account, admins included, and nothing switches it off.
An admin's writer keeps their words (no modesty rule); everyone else's, Managers' and clinics' too, doesn't."""
import json

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import content_policy as cp
from app import prompt_enhance as pe
from app.bulk import service as bs
from app.config import get_settings
from app.db import SessionLocal
from app.llm import set_transport
from app.models import AdminAudit, User
from tests.test_ai import oa
from tests.test_content_policy import _last, _run
from tests.test_implant import _client
from tests.test_magic_prompt import install


class _Eye:
    """The vision model: answers both picture questions, and says which one it was asked."""

    def __init__(self, minor=False, nude=False):
        self.minor, self.nude, self.asked = minor, nude, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": m} for m in ("fake-eye", "fake-writer")]})
        text = json.dumps(json.loads(request.content).get("messages", []))
        if "minor_nude_or_sexual" in text:
            self.asked.append("minor")
            return httpx.Response(200, json=oa(json.dumps({"minor_nude_or_sexual": self.minor, "why": "test"})))
        if "nudity_or_sexual" in text:
            self.asked.append("nudity")
            return httpx.Response(200, json=oa(json.dumps({"nudity_or_sexual": self.nude, "why": "test"})))
        return httpx.Response(500)


@pytest.fixture
def eye():
    def install_eye(**kw):
        e = _Eye(**kw)
        set_transport(httpx.MockTransport(e))
        return e

    yield install_eye
    set_transport(None)


@pytest.fixture
def member(client):
    with _client("sana@example.com") as c:
        yield c


@pytest.fixture
def manager(client):
    c = _client("bilal@example.com")
    with SessionLocal() as db:
        db.scalar(select(User).where(User.email == "bilal@example.com")).staff_role = "manager"
        db.commit()
    with c:
        yield c


@pytest.fixture
def surgeon(client):
    with _client("dr.ayesha@example.com", implant=True) as c:
        yield c


# ------------------------------------------------------------------ the words

def test_child_words_refuse_only_next_to_sexual_words():
    for bad in ("a naked teen girl", "nude children on a beach", "a sexy 15 year old", "schoolgirl in lingerie",
                "nangi bachi", "larki ki nangi tasveer", "ایک ننگی بچی", "kamsin larki bikini mein",
                "underage NSFW art", "a 12-year-old boy undressing"):
        assert cp.child_refusal(bad) == cp.CHILD_MSG, bad
        assert cp.quick_refusal(bad, admin=True) == cp.CHILD_MSG, bad
        assert cp.quick_refusal(bad, admin=False) == cp.CHILD_MSG, bad
        assert cp.prompt_refusal(bad, admin=True) == cp.CHILD_MSG, bad
    for fine in ("a boy flying a kite", "children playing cricket in Lahore", "ek bacha patang uda raha hai",
                 "ایک بچی کتاب پڑھ رہی ہے", "a teenager on a bicycle", "Lara on the beach in a bikini",
                 "a naked mole rat"):
        assert cp.child_refusal(fine) is None, fine
    # an adult admin request is still the admin's call
    assert cp.quick_refusal("a naked woman", admin=True) is None


def test_an_admin_is_refused_at_the_door_too(client, member):
    for c in (client, member):
        r = c.post("/api/images/generate", json={"prompt": "a naked teen girl on a bed"})
        assert r.status_code == 422 and r.json()["detail"] == cp.CHILD_MSG
    assert client.post("/api/images/generate", json={"prompt": "a boy flying a kite"}).status_code == 202


def test_a_bulk_instruction_with_a_child_is_refused():
    with pytest.raises(HTTPException) as e:
        bs.clean_options("edit", {"instruction": "undress the girl"})
    assert e.value.detail == cp.CHILD_MSG
    with pytest.raises(HTTPException):
        bs.clean_options("remove", {"words": "the girl's clothes, naked"})
    assert bs.clean_options("remove", {"words": "men"})["words"] == "men"


# ------------------------------------------------------------------ the finished picture

def test_an_admins_picture_gets_the_child_question(client, fast_driver, eye):
    e = eye(minor=True)
    client.post("/api/images/generate", json={"prompt": "a family at the beach"})
    _run(fast_driver)
    g, j = _last(client)
    assert g.status == "failed" and j.error == cp.CHILD_MSG and e.asked == ["minor"]
    assert g.file_path is None
    with SessionLocal() as db:
        row = db.scalar(select(AdminAudit).where(AdminAudit.action == "child_safety_refused"))
        assert row is not None and row.target_id == g.id

    e = eye(minor=False)
    client.post("/api/images/generate", json={"prompt": "a family at the beach"})
    _run(fast_driver)
    g, _ = _last(client)
    assert g.status == "ready" and e.asked == ["minor"]


def test_a_members_picture_keeps_the_full_nudity_check(member, fast_driver, eye):
    e = eye(nude=True)
    member.post("/api/images/generate", json={"prompt": "a woman on the beach at sunset"})
    _run(fast_driver)
    g, j = _last(member)
    assert g.status == "failed" and "nude, sexual or revealing" in j.error and e.asked == ["nudity"]


def test_the_picture_switch_doesnt_turn_the_child_check_off(member, fast_driver, eye, monkeypatch):
    monkeypatch.setattr(get_settings(), "content_check_pictures", False)
    e = eye(minor=True)
    member.post("/api/images/generate", json={"prompt": "a family at the beach"})
    _run(fast_driver)
    g, j = _last(member)
    assert g.status == "failed" and j.error == cp.CHILD_MSG and e.asked == ["minor"]


# ------------------------------------------------------------------ the writer's rules

def _rules_for(c) -> str:
    pe.clear_cache()
    fake = install(lambda p: {"prompt": "A lighthouse on black rocks at dusk"})
    try:
        r = c.post("/api/prompts/enhance", json={"prompt": "a lighthouse", "mode": "on"})
        assert r.status_code == 200, r.text
    finally:
        set_transport(None)
    return fake.seen[0]


def test_only_an_admins_writer_drops_the_modesty_rule(client, member, manager, surgeon):
    admin = _rules_for(client)
    assert "Prophet Muhammad ﷺ" in admin and "child" in admin and "modestly dressed" not in admin
    for c in (member, manager, surgeon):
        rules = _rules_for(c)
        assert "Prophet Muhammad ﷺ" in rules and "modestly dressed" in rules


def test_an_admins_job_writer_drops_it_too(client, member, fast_driver):
    for c, modest in ((client, False), (member, True)):
        pe.clear_cache()
        fake = install(lambda p: {"prompt": "A lighthouse on black rocks at dusk, beam through the mist"})
        try:
            c.post("/api/images/generate", json={"prompt": "a lighthouse", "magic_prompt": "on"})
            _run(fast_driver)
        finally:
            set_transport(None)
        assert fake.seen and "Prophet Muhammad ﷺ" in fake.seen[0]
        assert ("modestly dressed" in fake.seen[0]) is modest
