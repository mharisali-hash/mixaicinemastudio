"""Adult mode (owner, 22 Nov): an 18+ opt-in for made-up people. Words alone may be adult; anything built from a
picture keeps the modest rules. A child with sexual words and the revered figures stay refused, as for admins."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import content_policy as cp
from app import file_links
from app import prompt_enhance as pe
from app.db import SessionLocal
from app.llm import set_transport
from app.models import AdminAudit, Generation, User, utcnow
from tests.test_accounts import make_user
from tests.test_child_safety import _Eye, eye  # noqa: F401  (fixture)
from tests.test_content_policy import _last, _run, reviewer  # noqa: F401  (fixture)
from tests.test_implant import _client
from tests.test_magic_prompt import install
from tests.test_moderation import _upload

TICKS = {"confirm_18": True, "confirm_made_up": True, "confirm_review": True}


def _opt_in(c):
    r = c.patch("/api/auth/me/adult", json={"on": True, **TICKS})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def member(client):
    with _client("sana@example.com") as c:
        yield c


@pytest.fixture
def adult(member):
    _opt_in(member)
    return member


def test_switching_it_on_needs_all_three_ticks(member):
    assert member.get("/api/auth/me").json()["adult"] is False
    for missing in TICKS:
        r = member.patch("/api/auth/me/adult", json={"on": True, **TICKS, missing: False})
        assert r.status_code == 422, missing
    assert _opt_in(member)["adult"] is True
    r = member.patch("/api/auth/me/adult", json={"on": False})
    assert r.status_code == 200 and r.json()["adult"] is False
    with SessionLocal() as db:
        acts = [a.action for a in db.scalars(select(AdminAudit).where(AdminAudit.action.like("adult_mode_%"))
                                             .order_by(AdminAudit.created_at))]
    assert acts == ["adult_mode_on", "adult_mode_off"]


def test_words_alone_may_be_adult(adult, fast_driver, eye):  # noqa: F811
    e = eye()
    r = adult.post("/api/images/generate", json={"prompt": "a naked woman reclining on a sofa"})
    assert r.status_code == 202, r.text
    _run(fast_driver)
    g, _ = _last(adult)
    # marked for the moderators, and the picture gets only the child question
    assert g.status == "ready" and g.params.get("adult") is True and e.asked == ["minor"]
    with SessionLocal() as db:
        # never shared, not even by the ten-minute download link
        signed = file_links.url_for(db.get(Generation, g.id), "http://x").split("/api/files/")[1]
    assert adult.get(f"/api/files/{signed}").status_code == 404


def test_no_safety_negative_for_adult_words(client):
    who = make_user("lena@example.com", "Lena")
    with SessionLocal() as db:
        db.get(User, who).adult_mode_at = utcnow()
        db.commit()
        # a reference picture brings the modest rules back, safety words and all
        for extra, want in (({}, False), ({"reference_ids": ["3f2a9c1e-0000-4000-8000-000000000000"]}, True)):
            ctx = SimpleNamespace(db=db, job=SimpleNamespace(user_id=who), progress=lambda *a, **k: None)
            gen = SimpleNamespace(id="g1", kind="image", target_type="media", prompt="a woman on a beach",
                                  params={"created_by": {"user_id": who}, **extra})
            params = {}
            cp.check_before(ctx, gen, params)
            assert (cp.SAFE_NEGATIVE in params.get("negative", "")) is want
            assert bool(gen.params.get("adult")) is not want


def test_a_picture_in_keeps_the_modest_rules(adult):
    item = _upload(adult)
    r = adult.post("/api/images/img2img", json={"source_id": item["id"], "prompt": "make her naked"})
    assert r.status_code == 422 and r.json()["detail"] == cp.SEXUAL_MSG
    r = adult.post("/api/images/edit", json={"source_ids": [item["id"]], "instruction": "undress her"})
    assert r.status_code == 422 and r.json()["detail"] == cp.SEXUAL_MSG


def test_a_child_and_the_revered_stay_refused(adult, fast_driver, reviewer):  # noqa: F811
    r = adult.post("/api/images/generate", json={"prompt": "a naked teen girl on a bed"})
    assert r.status_code == 422 and r.json()["detail"] == cp.CHILD_MSG
    reviewer(depicts=True)
    assert adult.post("/api/images/generate", json={"prompt": "Prophet Musa on a mountain"}).status_code == 202
    _run(fast_driver)
    g, j = _last(adult)
    assert g.status == "failed" and j.error == cp.RELIGIOUS_MSG


def test_the_writer_drops_the_modesty_rule_only_for_words(adult):
    def rules(kind):
        pe.clear_cache()
        fake = install(lambda p: {"prompt": "A woman on a sofa"})
        try:
            r = adult.post("/api/prompts/enhance", json={"prompt": "a woman", "mode": "on", "kind": kind})
            assert r.status_code == 200, r.text
        finally:
            set_transport(None)
        return fake.seen[0]

    text, edit = rules("image"), rules("edit")
    assert "Prophet Muhammad ﷺ" in text and "child" in text and "modestly dressed" not in text
    assert "modestly dressed" in edit


def test_the_team_can_switch_it_off(client, adult):
    me = adult.get("/api/auth/me").json()
    manager = _client("mo@example.com")
    with SessionLocal() as db:
        db.scalar(select(User).where(User.email == "mo@example.com")).staff_role = "manager"
        db.commit()

    listed = {u["id"]: u for u in manager.get("/api/moderation/users").json()["users"]}
    assert listed[me["id"]]["adult"] is True
    r = manager.post(f"/api/moderation/users/{me['id']}/adult-off", json={"reason": "Used a real photo"})
    assert r.status_code == 200, r.text
    body = adult.get("/api/auth/me").json()
    assert body["adult"] is False and body["adult_off"] is True
    r = adult.post("/api/images/generate", json={"prompt": "a naked woman"})
    assert r.status_code == 422 and r.json()["detail"] == cp.SEXUAL_MSG
    r = adult.patch("/api/auth/me/adult", json={"on": True, **TICKS})
    assert r.status_code == 403 and "switched off" in r.json()["detail"]
    assert adult.get("/api/account/notices").json()[0]["kind"] == "adult_off"

    # only an admin lifts it, and then the person ticks the three boxes again
    assert manager.post(f"/api/moderation/users/{me['id']}/adult-allow").status_code == 403
    assert client.post(f"/api/moderation/users/{me['id']}/adult-allow").status_code == 200
    assert adult.get("/api/auth/me").json()["adult"] is False
    assert _opt_in(adult)["adult"] is True


def test_review_can_filter_for_adult_work(client, member, fast_driver, eye):  # noqa: F811
    eye()
    member.post("/api/images/generate", json={"prompt": "a lighthouse"})
    _run(fast_driver)
    _opt_in(member)
    member.post("/api/images/generate", json={"prompt": "a nude figure study"})
    _run(fast_driver)
    items = client.get("/api/moderation/items", params={"adult": True, "state": "all"}).json()["items"]
    assert len(items) == 1 and items[0]["adult"] is True and "nude" in items[0]["prompt"]


def test_an_admin_is_unaffected(client, fast_driver, eye):  # noqa: F811
    eye()
    assert client.post("/api/images/generate", json={"prompt": "a naked woman"}).status_code == 202
    _run(fast_driver)
    g, _ = _last(client)
    assert g.status == "ready" and "adult" not in g.params
