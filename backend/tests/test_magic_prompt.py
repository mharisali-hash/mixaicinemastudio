"""UI polish P1: magic prompt, the "auto" model and the Generate-button estimate. Fake LLM only."""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import estimate as est
from app import models_catalog as mc
from app import prompt_enhance as pe
from app.db import SessionLocal
from app.llm import set_transport
from app.models import Generation, Workspace, new_id, utcnow
from tests.test_storyboard import _run_all

URDU = "خوش آمدید"


class FakeWriter:
    """OpenAI-compatible answers; `reply(prompt_text) -> dict | Exception`."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        # the system message of each call, to see which rules the writer got
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content)
        self.seen.append(body["messages"][0]["content"])
        user = body["messages"][-1]["content"].split("Prompt:\n", 1)[-1]
        out = self.reply(user)
        if isinstance(out, Exception):
            raise out
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)},
                                                      "finish_reason": "stop"}], "usage": {}})


@pytest.fixture(autouse=True)
def _clean():
    pe.clear_cache()
    yield
    pe.clear_cache()
    set_transport(None)


def install(reply) -> FakeWriter:
    fake = FakeWriter(reply)
    set_transport(httpx.MockTransport(fake))
    return fake


# ------------------------------------------------------------------ enhancer

def test_heuristic():
    assert pe.needs_enhance("a cat")
    assert pe.needs_enhance('a shop sign that says "Open all night long for every hungry traveller in town"')
    detailed = "a fisherman mending nets on a quiet harbour wall, golden hour light, 85mm lens, shallow depth"
    assert not pe.needs_enhance(detailed)
    assert pe.needs_enhance("my grandmother and her two sisters sitting together at the old kitchen table "
                            "talking about the village where they grew up")


def test_keeps_quoted_urdu_and_english(client):
    prompt = f'a tea stall poster saying "{URDU}" and "Chai 24/7"'
    fake = install(lambda p: {"prompt": f'A hand-painted tea stall poster with "{URDU}" in bold Nastaliq above '
                                        f'"Chai 24/7", warm tungsten light, steam rising', "notes": "added light"})
    r = client.post("/api/prompts/enhance", json={"prompt": prompt, "kind": "image", "model": "qwen_image_2512"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changed"] and f'"{URDU}"' in out["enhanced"] and '"Chai 24/7"' in out["enhanced"]
    assert out["notes"] == "added light" and fake.calls == 1

    # a "helpful" translation of the quote is refused: the user's words come back
    pe.clear_cache()
    install(lambda p: {"prompt": 'A tea stall poster reading "Welcome" and "Chai 24/7", warm light'})
    out = client.post("/api/prompts/enhance", json={"prompt": prompt, "mode": "on"}).json()
    assert out == {"enhanced": prompt, "changed": False, "notes": "kept your prompt: the enhancer changed quoted text"}


def test_auto_skips_detailed_prompts_and_on_forces(client):
    fake = install(lambda p: {"prompt": p + ", soft rim light"})
    detailed = "a fisherman mending nets on a quiet harbour wall, golden hour light, 85mm lens, shallow depth"
    out = client.post("/api/prompts/enhance", json={"prompt": detailed, "mode": "auto"}).json()
    assert out == {"enhanced": detailed, "changed": False, "notes": None} and fake.calls == 0
    out = client.post("/api/prompts/enhance", json={"prompt": detailed, "mode": "on"}).json()
    assert out["changed"] and out["enhanced"].endswith("soft rim light") and fake.calls == 1


def test_fallback_when_the_llm_fails(client):
    install(lambda p: httpx.ReadTimeout("slow"))
    out = client.post("/api/prompts/enhance", json={"prompt": "a red bicycle", "kind": "video"}).json()
    assert out == {"enhanced": "a red bicycle", "changed": False, "notes": "enhancer unavailable"}

    set_transport(httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "boom"})))
    out = client.post("/api/prompts/enhance", json={"prompt": "a red bicycle"}).json()
    assert out["changed"] is False and out["notes"] == "enhancer unavailable"


def test_cache_and_video_prompt_rules(client):
    seen = []

    def reply(p):
        seen.append(p)
        return {"prompt": "Slow push in on a red bicycle leaning on a wall, a bell rings, distant traffic hum"}

    fake = install(reply)
    body = {"prompt": "a red bicycle", "kind": "video"}
    a = client.post("/api/prompts/enhance", json=body).json()
    b = client.post("/api/prompts/enhance", json=body).json()
    assert a == b and a["changed"] and fake.calls == 1
    client.post("/api/prompts/enhance", json={**body, "kind": "image"})
    assert fake.calls == 2  # a different kind is a different request

    msgs = pe.messages("a red bicycle", "video")
    assert "camera" in msgs[0]["content"] and "sound" in msgs[0]["content"] and "160 words" in msgs[0]["content"]
    assert "120 words" in pe.messages("x", "image")[0]["content"]


# ------------------------------------------------------------------ generators

def _gens(body):
    with SessionLocal() as db:
        return [db.get(Generation, i["generation_id"]) for i in body["items"]]


def test_magic_prompt_in_image_generate(client, fast_driver):
    fake = install(lambda p: {"prompt": "A lighthouse on black rocks at dusk, beam cutting through sea mist"})

    off = client.post("/api/images/generate", json={"prompt": "a lighthouse", "magic_prompt": "off"}).json()
    assert off["magic_prompt"] == {"mode": "off", "status": "off"}

    on = client.post("/api/images/generate", json={"prompt": "a lighthouse", "style": "cinematic", "count": 2,
                                                   "magic_prompt": "on"}).json()
    assert on["magic_prompt"]["status"] == "pending" and on["model_resolved"] == "zimage_turbo"

    client_side = client.post("/api/images/generate", json={"prompt": "a lighthouse, enhanced already",
                                                            "prompt_enhanced": True}).json()
    # the test env defaults to off; the dock flag still records "client" when a mode is given
    assert client_side["magic_prompt"]["status"] == "off"
    flagged = client.post("/api/images/generate", json={"prompt": "a lighthouse", "magic_prompt": "auto",
                                                        "prompt_enhanced": True}).json()
    assert flagged["magic_prompt"] == {"mode": "auto", "status": "client"}

    _run_all(fast_driver)
    assert fake.calls == 1  # two images, one enhancer call (cache)
    g_off, = _gens(off)
    assert g_off.prompt == "a lighthouse" and "original_prompt" not in g_off.params
    for g in _gens(on):
        assert g.status == "ready"
        assert g.prompt.startswith("A lighthouse on black rocks at dusk, beam cutting through sea mist. ")
        assert "anamorphic" in g.prompt  # the style suffix survives the swap
        assert g.params["original_prompt"] == "a lighthouse"
        assert g.params["enhanced_prompt"].startswith("A lighthouse on black rocks")
        assert g.params["magic_prompt"]["status"] == "done" and g.params["magic_prompt"]["changed"]
    g_flag, = _gens(flagged)
    assert g_flag.prompt == "a lighthouse"


def test_magic_prompt_auto_and_failure_in_job(client, fast_driver):
    fake = install(lambda p: httpx.ConnectError("down"))
    detailed = "a fisherman mending nets on a quiet harbour wall, golden hour light, 85mm lens, shallow depth"
    keep = client.post("/api/images/generate", json={"prompt": detailed, "magic_prompt": "auto"}).json()
    short = client.post("/api/images/generate", json={"prompt": "a fox", "magic_prompt": "auto"}).json()
    _run_all(fast_driver)
    assert fake.calls == 1  # only the short one asked
    g_keep, = _gens(keep)
    assert g_keep.prompt == detailed and g_keep.params["magic_prompt"]["changed"] is False
    g_short, = _gens(short)
    assert g_short.status == "ready" and g_short.prompt == "a fox"
    assert g_short.params["magic_prompt"]["notes"] == "enhancer unavailable"


def test_magic_prompt_in_video_generate(client, fast_driver):
    install(lambda p: {"prompt": "Handheld tracking shot of a boy flying a red kite on a windy beach, gulls cry"})
    r = client.post("/api/videos/generate", json={"prompt": "kid with kite", "duration_s": 3, "magic_prompt": "on",
                                                  "model": "auto"})
    assert r.status_code == 202, r.text
    assert r.json()["model_resolved"] == "ltx23_distilled"
    _run_all(fast_driver)
    with SessionLocal() as db:
        g = db.get(Generation, r.json()["generation_id"])
        assert g.prompt.startswith("Handheld tracking shot") and g.params["original_prompt"] == "kid with kite"
        assert g.params["model_requested"] == "auto" and g.params["model_resolved"] == "ltx23_distilled"


# ------------------------------------------------------------------ auto model

def test_auto_resolution_cases():
    assert mc.resolve_auto("image", f'a poster with "{URDU}"')[0].id == "qwen_image_2512"
    assert mc.resolve_auto("image", "ایک چائے کی دکان")[0].id == "qwen_image_2512"
    assert mc.resolve_auto("image", "a shop sign above the door")[0].id == "qwen_image_2512"
    assert mc.resolve_auto("image", "a meadow at noon", count=4)[0].id == "flux2_klein"
    assert mc.resolve_auto("image", 'a logo for "Acme"', count=4)[0].id == "qwen_image_2512"
    assert mc.resolve_auto("image", "a meadow at noon")[0].id == "zimage_turbo"
    assert mc.resolve_auto("image", "a textured wall")[0].id == "zimage_turbo"  # "texture" isn't "text"
    assert mc.resolve_auto("edit", "make it night", refs=2)[0].id == "qwen_image_edit_2511"
    assert mc.resolve_auto("video", "a storm")[0].id == "ltx23_distilled"
    assert mc.resolve_auto("video", "a storm", quality="hq")[0].id == "ltx23_hq"


def test_auto_in_routes_and_catalog(client):
    r = client.post("/api/images/generate", json={"prompt": 'a cinema marquee reading "OPEN"', "model": "auto",
                                                  "speed": "turbo"})
    assert r.status_code == 202, r.text
    g, = _gens(r.json())
    assert r.json()["model_resolved"] == "qwen_image_2512" and g.params["model"] == "qwen_image_2512"
    assert g.params["speed"] == "turbo" and g.params["model_auto_reason"] == "text in the prompt"

    r = client.post("/api/images/generate", json={"prompt": "a meadow", "model": "auto", "count": 4, "speed": "turbo"})
    assert r.status_code == 202, r.text  # the speed doesn't apply to FLUX klein; auto drops it
    assert {x.params["model"] for x in _gens(r.json())} == {"flux2_klein"}
    assert "speed" not in _gens(r.json())[0].params

    r = client.post("/api/images/generate", json={"prompt": "a meadow"})
    assert r.json()["model_resolved"] == "zimage_turbo" and "model_requested" not in _gens(r.json())[0].params

    autos = [m for m in client.get("/api/models").json() if m["id"] == "auto"]
    assert [m["type"] for m in autos] == ["image", "edit", "video", "audio"]
    assert all(m["description"] == mc.AUTO_DESCRIPTION and m["available"] for m in autos)


# ------------------------------------------------------------------ estimate

def _seed(db, ws, params, *, minutes_ago=0):
    g = Generation(id=new_id(), workspace_id=ws, target_type="media", target_id=new_id(), kind="image", version=1,
                   status="ready", prompt="x", params=params, seed=1)
    db.add(g)
    db.flush()
    g.updated_at = utcnow() - timedelta(minutes=minutes_ago)
    return g


def test_estimate_rough_then_measured(client):
    r = client.get("/api/estimate?kind=image&model=zimage_turbo&count=4")
    rough = r.json()
    assert r.status_code == 200 and rough["basis"] == "rough" and rough["samples"] == 0
    assert rough["load_s"] == est.LOAD_S["zimage_turbo"]  # nothing ran yet, so the model must load
    assert rough["low_s"] < rough["high_s"]
    # 7 s warm each: 4 x 7 x 0.75 + load .. 4 x 7 x 1.6 + load
    assert rough["low_s"] == 21 + 15 and rough["high_s"] == 45 + 15

    with SessionLocal() as db:
        ws = db.scalars(select(Workspace.id)).first()
        for i, secs in enumerate((4.0, 5.0, 6.0, 9.0, 5.5)):
            _seed(db, ws, {"model": "zimage_turbo", "width": 1024, "height": 1024,
                           "comfy": {"template": "zimage_t2i", "gpu_seconds": secs}}, minutes_ago=10 + i)
        # video history: 40 s of GPU for 5 s of clip -> 8 s per output second
        for i in range(3):
            _seed(db, ws, {"model": "ltx23_distilled", "width": 832, "height": 480, "duration_s": 5.0,
                           "comfy": {"template": "ltx23_i2v", "gpu_seconds": 40.0 + i * 5}}, minutes_ago=30 + i)
        db.commit()

    m = client.get("/api/estimate?kind=image&model=zimage_turbo&count=4").json()
    assert m["basis"] == "measured" and m["samples"] == 5
    assert m["load_s"] == 0  # Z-Image was the last thing on the GPU
    # median 5.5 x 4; p80 is 6.0 x 4 = 24, but a range is never narrower than 15 % over the median
    assert m["low_s"] == 22 and m["high_s"] == 26

    # a different size of the same model scales by pixels
    big = client.get("/api/estimate?kind=image&model=zimage_turbo&width=2048&height=2048").json()
    assert big["basis"] == "measured" and big["low_s"] == 22

    v = client.get("/api/estimate?kind=video&model=ltx23_distilled&duration_s=10").json()
    assert v["basis"] == "measured" and v["samples"] == 3
    assert v["load_s"] == est.LOAD_S["ltx23_distilled"]
    assert v["low_s"] == 90 + 45 and v["high_s"] == 149  # 9 s/s median over 10 s; high 103.5 + 45

    q = client.get("/api/estimate?kind=image&model=qwen_image_2512&speed=full").json()
    assert q["basis"] == "rough" and q["load_s"] == 35
    lightning = client.get("/api/estimate?kind=image&model=qwen_image_2512").json()
    assert q["high_s"] > lightning["high_s"]

    up = client.get("/api/estimate?kind=upscale&model=seedvr2&duration_s=10").json()
    assert up["basis"] == "rough" and up["model"] == "upscale_seedvr2" and up["high_s"] > up["low_s"]
    assert client.get("/api/estimate?kind=image&model=nope").status_code == 422


def test_quick_carries_the_marker_and_outline_uses_it(client):
    from app.autopilot import outline_messages
    from app.models import Job, Project

    r = client.post("/api/quick", json={"prompt": "a lost kite finds its way home", "duration_s": 10,
                                        "magic_prompt": "on", "image_model": "auto"})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        job = db.get(Job, r.json()["job"]["id"])
        assert job.payload["magic_prompt"] == {"mode": "on", "kind": "quick", "status": "pending",
                                               "style": "cinematic"}
        p = db.get(Project, r.json()["project"]["id"])
        assert "image_model" not in (p.settings or {})  # auto there means the default for now
        text = outline_messages(p, "cinematic", True, 10, expanded="A red kite drifts over rooftops")[1]["content"]
        assert "a lost kite finds its way home" in text and "A red kite drifts over rooftops" in text


def test_prompt_correction_on_an_edit(client, fast_driver):
    from tests.test_camera_mentions import upload

    fake = install(lambda p: {"prompt": "Make the sky a deep night blue with stars; keep the boat and the people "
                                        "exactly as they are"})
    a, b = upload(client), upload(client)
    # no choice sent (API and MCP edits): the instruction goes exactly as written
    plain = client.post("/api/images/edit", json={"source_ids": [a["id"]], "instruction": "make it night"}).json()
    asked = client.post("/api/images/edit", json={"source_ids": [a["id"], b["id"]], "instruction": "make it night",
                                                  "magic_prompt": "on"}).json()
    _run_all(fast_driver)
    assert fake.calls == 1
    g_plain, = _gens(plain)
    assert g_plain.prompt == "make it night" and g_plain.params["magic_prompt"]["status"] == "off"
    g, = _gens(asked)
    # the corrected instruction replaces the user's words (a mention's "Picture 2" line would stay in front:
    # test_splice_finds_the_words_after_a_preamble)
    assert g.prompt.startswith("Make the sky a deep night blue with stars")
    assert "make it night" not in g.prompt and g.params["original_prompt"] == "make it night"
    # the writer was asked for an edit, not a fresh picture
    assert "image editing model" in fake.seen[0]


def test_splice_finds_the_words_after_a_preamble():
    assert pe.splice("Picture 2 is a hat. make it night", "make it night", "Night sky") == "Picture 2 is a hat. Night sky"
    assert pe.splice("make it night. brand suffix", "make it night", "Night sky") == "Night sky. brand suffix"
    assert pe.splice("something else", "make it night", "Night sky") is None
