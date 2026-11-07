"""Character Looks (7 Nov): looks with their own pictures, Look Packs, a default, your own photo, performance."""
from sqlalchemy import select

from app import character_looks as cl
from app.db import SessionLocal
from app.models import Character, Generation
from tests.test_library import png_bytes
from tests.test_storyboard import _run_all


def _character(client, name="Ayesha") -> dict:
    r = client.post("/api/characters", json={"name": name, "description": "a woman in her thirties, short dark hair"})
    assert r.status_code == 201, r.text
    return r.json()


def _with_portrait(client, fast_driver) -> dict:
    c = _character(client)
    r = client.post("/api/generations", json={"target_type": "character", "target_id": c["id"], "kind": "portrait",
                                              "prompt": "head and shoulders portrait"})
    assert r.status_code == 201, r.text
    _run_all(fast_driver)
    return c


def test_a_look_needs_a_portrait_to_draw_from(client):
    c = _character(client)
    r = client.post(f"/api/characters/{c['id']}/looks", json={"name": "Office", "outfit": "a navy suit"})
    assert r.status_code == 409 and "portrait first" in r.json()["detail"]
    # a pack is refused whole: no half-made looks left behind
    r = client.post(f"/api/characters/{c['id']}/looks/pack", json={"pack": "business"})
    assert r.status_code == 409
    assert client.get(f"/api/characters/{c['id']}/looks").json()["looks"] == []


def test_looks_have_their_own_pictures_and_one_default(client, fast_driver):
    c = _with_portrait(client, fast_driver)
    r = client.post(f"/api/characters/{c['id']}/looks/pack", json={"pack": "traditional"})
    assert r.status_code == 201, r.text
    looks = r.json()["looks"]
    assert [x["name"] for x in looks] == ["Shalwar kameez", "Sherwani", "Dupatta", "Waistcoat"]
    assert [x["is_default"] for x in looks] == [True, False, False, False]
    with SessionLocal() as db:
        g = db.scalars(select(Generation).where(Generation.target_type == "character_look",
                                                Generation.target_id == looks[1]["id"])).one()
        portrait = db.scalars(select(Generation).where(Generation.target_type == "character",
                                                       Generation.kind == "portrait")).first()
        # drawn from the portrait, keeping the face, now in the sherwani
        assert g.params["reference_ids"] == [portrait.id] and "sherwani" in g.prompt and "Ayesha" in g.prompt
    _run_all(fast_driver)
    listed = client.get(f"/api/characters/{c['id']}/looks").json()
    assert all(x["picture"] and x["picture"]["status"] == "ready" for x in listed["looks"])
    assert {p["id"] for p in listed["packs"]} == set(cl.PACKS)

    # approve a picture through the usual route: one approved per look
    pic = listed["looks"][1]["picture"]["id"]
    assert client.post(f"/api/generations/{pic}/approve").status_code == 200
    r = client.post(f"/api/characters/{c['id']}/looks/{looks[1]['id']}/generate", json={"note": "warmer light"})
    assert r.status_code == 201
    _run_all(fast_driver)

    r = client.patch(f"/api/characters/{c['id']}/looks/{looks[2]['id']}", json={"is_default": True, "name": "Garden"})
    assert r.json()["is_default"] and r.json()["name"] == "Garden"
    listed = client.get(f"/api/characters/{c['id']}/looks").json()["looks"]
    assert [x["is_default"] for x in listed] == [False, False, True, False]
    # deleting the default hands it on
    assert client.delete(f"/api/characters/{c['id']}/looks/{looks[2]['id']}").status_code == 204
    listed = client.get(f"/api/characters/{c['id']}/looks").json()["looks"]
    assert len(listed) == 3 and sum(x["is_default"] for x in listed) == 1


def test_your_own_photo_as_a_look_needs_consent(client, fast_driver):
    c = _with_portrait(client, fast_driver)
    look = client.post(f"/api/characters/{c['id']}/looks", json={"name": "Wedding", "make": False}).json()
    up = client.post("/api/media/upload", files={"file": ("me.png", png_bytes(), "image/png")}).json()
    media_id = (up.get("item") or up.get("items", [up])[0])["id"]
    r = client.post(f"/api/characters/{c['id']}/looks/{look['id']}/upload",
                    json={"media_id": media_id, "consent": {"name": "", "statement": "x"}})
    assert r.status_code == 422
    from app.api.characters import PHOTO_CONSENT

    for _ in range(2):  # a second upload replaces the approved one without breaking the one-approved rule
        r = client.post(f"/api/characters/{c['id']}/looks/{look['id']}/upload",
                        json={"media_id": media_id, "consent": {"name": "Ayesha Khan", "statement": PHOTO_CONSENT}})
        assert r.status_code == 201, r.text
        assert r.json()["picture"]["status"] == "approved"


def test_performance_is_saved_and_read_by_the_prompts(client):
    c = _character(client)
    r = client.patch(f"/api/characters/{c['id']}", json={"performance": {"expressiveness": "lively",
                                                                          "gestures": "nods a lot,  uses hands"}})
    assert r.status_code == 200, r.text
    assert r.json()["performance"] == {"expressiveness": "lively", "gestures": "nods a lot, uses hands"}
    with SessionLocal() as db:
        text = cl.performance_text(db.get(Character, c["id"]))
    assert text.startswith("a lively, expressive face") and text.endswith("nods a lot, uses hands.")
    assert cl.performance_text(None) == ""
    assert client.patch(f"/api/characters/{c['id']}", json={"performance": {"expressiveness": "wild"}}).status_code == 422
