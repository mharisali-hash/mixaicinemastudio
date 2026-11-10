"""Prompt templates: the shipped JSON, previews, and the /prompt-templates aliases."""
import importlib.util
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import library as lib_api
from app import models_catalog
from app.api.quick import QuickIn
from app.library import ASPECTS, STYLES
from app.main import app

DIR = Path(lib_api.TEMPLATE_DIR)
SLOT = re.compile(r"\[([^\[\]]{1,60})\]")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_prompt_template_previews.py"


def _load(kind):
    return json.loads((DIR / f"{kind}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind,minimum", [("image", 24), ("video", 16)])
def test_template_json_is_sound(kind, minimum):
    data = _load(kind)
    rows, cats = data["templates"], data["categories"]
    assert len(rows) >= minimum and len(set(cats)) == len(cats)
    assert {t["category"] for t in rows} == set(cats)  # every category used, none unknown
    for t in rows:
        assert t["id"].startswith(f"{kind}-") and re.fullmatch(r"[a-z0-9-]+", t["id"])
        assert t["title"] and t["description"] and t["tags"]
        d = t["defaults"]
        if t.get("target") in ("image-edit", "image-control", "image-brand"):
            continue  # test_edit_templates, test_control_templates, test_brand_templates.py
        texts = [d.get("prompt_scaffold", ""), d.get("logline_hint", ""), t.get("preview_prompt", "")]
        slots = set(SLOT.findall(d["prompt_scaffold"]))
        assert 2 <= len(slots) <= 4, t["id"]
        for text in texts:
            missing = set(SLOT.findall(text)) - set(t["examples"])
            assert not missing, f"{t['id']}: no example for {missing}"
        if kind == "image":
            assert d["aspect"] in ASPECTS and d["style"] in STYLES and 1 <= d["count"] <= 4
        elif "authoring_mode" not in d:
            QuickIn(prompt=d["prompt_scaffold"], duration_s=d["duration_s"], aspect_ratio=d["aspect_ratio"],
                    style=d["style"], dialogue=d["dialogue"])
            assert 5 <= d["duration_s"] <= 120 and t.get("preview_prompt")


def test_ids_unique_and_text_templates_use_qwen():
    rows = _load("image")["templates"] + _load("video")["templates"]
    ids = [t["id"] for t in rows]
    assert len(ids) == len(set(ids))
    qwen = [t for t in rows if t["defaults"].get("model") == "qwen_image_2512"]
    assert 1 <= len(qwen) <= 16 and all('"[' in t["defaults"]["prompt_scaffold"] for t in qwen)
    urdu = [t for t in qwen if re.search(r"[؀-ۿ]", json.dumps(t["examples"], ensure_ascii=False))]
    assert len(urdu) >= 2


def test_preview_script_fills_every_template():
    spec = importlib.util.spec_from_file_location("gen_previews", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for t in mod.load_templates():
        r = mod.recipe(t)
        assert "[" not in r["prompt"] and r["params"]["width"] % 64 == 0
        qwen = t["defaults"].get("model") == "qwen_image_2512" or t.get("target") == "image-brand"
        assert r["model"] == ("qwen_image_2512" if qwen else "zimage_turbo")


def test_aliases_list_and_start(client):
    old = client.get("/api/templates", params={"type": "image"}).json()
    new = client.get("/api/prompt-templates", params={"type": "image"}).json()
    assert [t["id"] for t in old] == [t["id"] for t in new]
    cats = _load("image")["categories"]
    assert [cats.index(t["category"]) for t in new] == sorted(cats.index(t["category"]) for t in new)
    poster = next(t for t in new if t["id"] == "image-poster")
    assert poster["examples"]["TITLE"] and poster["tags"]

    r = client.post("/api/prompt-templates/image-poster/start").json()
    assert r["target"] == "image" and r["prefill"]["model"] == "qwen_image_2512"
    assert r["prefill"]["examples"]["TITLE"]
    assert client.post("/api/prompt-templates/nope/start").status_code == 404


def test_preview_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setattr(lib_api, "PREVIEW_DIR", tmp_path)
    (tmp_path / "image-portrait.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPfake")
    rows = {t["id"]: t for t in client.get("/api/prompt-templates").json()}
    url = rows["image-portrait"]["preview_url"]
    assert url.startswith("/api/prompt-templates/image-portrait/preview?v=")
    assert rows["image-food"]["preview_url"] is None

    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert "max-age" in r.headers["cache-control"] and r.content.endswith(b"fake")
    assert client.get("/api/templates/image-portrait/preview").status_code == 200
    assert client.get("/api/prompt-templates/image-food/preview").status_code == 404  # no file yet
    assert client.get("/api/prompt-templates/nope/preview").status_code == 404

    # an id can't walk out of the previews folder, even if one slipped into the catalogue
    outside = tmp_path.parent / "secret.webp"
    outside.write_bytes(b"x")
    monkeypatch.setattr(lib_api, "templates", lambda: {"../secret": {"id": "../secret"}})
    assert lib_api.preview_file("../secret") is None

    with TestClient(app) as anon:
        assert anon.get("/api/prompt-templates/image-portrait/preview").status_code == 401


def test_shipped_previews_are_small():
    files = list((DIR / "previews").glob("*.webp"))
    ids = {t["id"] for t in _load("image")["templates"] + _load("video")["templates"]}
    assert {f.stem for f in files} <= ids
    assert sum(f.stat().st_size for f in files) < 3.5 * 1024 * 1024


EDIT_IDS = {"image-edit-outfit-swap", "image-edit-hairstyle", "image-edit-hairstyle-grid", "image-edit-headshot",
            "image-edit-retouch", "image-edit-remove-object", "image-edit-remove-by-words",
            "image-edit-relight", "image-edit-product-scene",
            "image-edit-line-art", "image-edit-giant-beach", "image-edit-helicopter", "image-edit-icy-plunge",
            "image-edit-3d-billboard", "image-edit-miniature-factory", "image-edit-exploded-view",
            "image-edit-photo-grid", "image-edit-across-time", "image-edit-front-page", "image-edit-mini-me",
            "image-edit-tiny-me", "image-edit-pet-cover", "image-edit-style-breakdown", "image-edit-trend-collage",
            "image-edit-virtual-tryon", "image-edit-depth-map", "image-edit-edge-map", "image-edit-restyle",
            "image-edit-image-prompt"}
CONTROL_IDS = {"image-control-depth", "image-control-sketch", "image-control-pose", "image-control-edges"}


def test_edit_templates():
    rows = [t for t in _load("image")["templates"] if t.get("target") == "image-edit"]
    assert {t["id"] for t in rows} == EDIT_IDS
    for t in rows:
        d = t["defaults"]
        assert "prompt_scaffold" not in d and t["id"].startswith("image-edit-")
        assert 1 <= t["refs_required"] <= len(t["ref_labels"]) <= 3, t["id"]
        assert all(isinstance(x, str) and x for x in t["ref_labels"])
        text = d["instruction_scaffold"]
        assert "image 1" in text and len(text) <= 2000
        slots = set(SLOT.findall(text))
        assert len(slots) <= 4
        assert not (slots | set(SLOT.findall(t["preview_prompt"]))) - set(t["examples"]), t["id"]
        assert d.get("aspect") in (None, *ASPECTS) and 1 <= d["count"] <= 4
        for o in t.get("options", []):
            assert o["slot"] in slots and o["label"] and len(o["choices"]) >= 2, t["id"]
            for c in o["choices"]:
                assert c["label"] and c["value"] and "[" not in c["value"]
                assert c.get("aspect") in (None, *ASPECTS)
    outfit = next(t for t in rows if t["id"] == "image-edit-outfit-swap")
    assert outfit["ref_labels"] == ["Person", "Garment"] and outfit["refs_required"] == 2
    hair = next(t for t in rows if t["id"] == "image-edit-hairstyle")
    assert all(len(o["choices"]) >= 12 for o in hair["options"])
    grid = next(t for t in rows if t["id"] == "image-edit-hairstyle-grid")
    assert grid["defaults"]["aspect"] == "3:4" and "3×4" in grid["defaults"]["instruction_scaffold"]
    era = next(t for t in rows if t["id"] == "image-edit-across-time")["options"][0]["choices"]
    assert {"Victorian", "1920s", "1950s", "1980s", "Futuristic"} <= {c["label"] for c in era}
    assert next(t for t in rows if t["id"] == "image-edit-pet-cover")["ref_labels"] == ["Pet"]


def test_every_template_loads_and_names_a_real_model():
    rows = [t for t in lib_api.templates().values() if t["type"] == "image"]
    assert len(rows) == 91 and sum(t["target"] == "image-edit" for t in rows) == len(EDIT_IDS)
    assert {t["id"] for t in rows if t["target"] == "image-control"} == CONTROL_IDS
    for t in rows:
        want = "edit" if t["target"] in ("image-edit", "image-brand") else "image"
        model = t["defaults"].get("model")
        if want == "edit":
            assert model, t["id"]
        if model:
            assert models_catalog.MODELS[model].type == want, t["id"]
        lib_api.TemplateOut.model_validate(lib_api._template_out(t).model_dump())


def test_list_back_compat_and_edit_fields(client):
    rows = {t["id"]: t for t in client.get("/api/prompt-templates", params={"type": "image"}).json()}
    old = rows["image-portrait"]
    assert old["target"] == "image-generate" and old["refs_required"] == 0 and old["ref_labels"] == [] and old["options"] == []
    swap = rows["image-edit-outfit-swap"]
    assert swap["target"] == "image-edit" and swap["ref_labels"] == ["Person", "Garment"] and swap["refs_required"] == 2
    assert rows["image-edit-hairstyle"]["options"][0]["slot"] == "hairstyle"
    video = client.get("/api/prompt-templates", params={"type": "video"}).json()
    assert all(t["target"] is None for t in video)


def test_start_edit_template(client):
    r = client.post("/api/prompt-templates/image-edit-outfit-swap/start").json()
    assert r["target"] == "image-edit"
    p = r["prefill"]
    assert p["model"] == "qwen_image_edit_2511" and p["ref_labels"] == ["Person", "Garment"] and p["refs_required"] == 2
    assert p["instruction"].startswith("Dress the person in image 1") and p["placeholders"] == []
    assert "prompt" not in p and p["template_id"] == "image-edit-outfit-swap"

    r = client.post("/api/prompt-templates/image-edit-headshot/start").json()
    assert r["prefill"]["placeholders"] == ["attire", "background"] and r["prefill"]["aspect"] == "3:4"
    assert [o["slot"] for o in r["prefill"]["options"]] == ["attire", "background"]
    assert r["prefill"]["examples"]["attire"]

    # generate cards are unchanged
    r = client.post("/api/prompt-templates/image-cafe-menu/start").json()
    assert r["target"] == "image" and r["prefill"]["model"] == "qwen_image_2512" and "[NAME]" in r["prefill"]["prompt"]
