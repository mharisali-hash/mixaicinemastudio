"""Image tools behind Edit Image templates (contract v30): line art / depth / edges, LaMa, IC-Light, CatVTON."""
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app import image_tools as it
from app import models_catalog as mc
from app.config import get_settings
from app.db import SessionLocal
from app.drivers.comfy import TIMEOUTS, plan_generation
from app.models import Generation
from app.workflows import build, check_template, load
from tests.test_library import png_bytes, upload
from tests.test_storyboard import _run_all

FIXTURE = Path(__file__).parent / "fixtures" / "comfy_object_info.json"


def info():
    return json.loads(FIXTURE.read_text())


def mask_url(size=(320, 200), box=(100, 60, 180, 140), fmt="PNG") -> str:
    m = Image.new("L", size, 0)
    m.paste(255, box)
    buf = io.BytesIO()
    m.save(buf, fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def edit(client, **body):
    return client.post("/api/images/edit", json=body)


def gen_params(item: dict) -> dict:
    with SessionLocal() as db:
        return db.get(Generation, item["generation_id"]).params


@pytest.fixture
def photo(client):
    return upload(client, "street.png", png_bytes((320, 200)), "image/png").json()


@pytest.fixture
def nc_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "image_allow_noncommercial", False)


# ------------------------------------------------------------------ workflows

@pytest.mark.parametrize("tool", list(it.TOOLS))
def test_tool_templates_build_and_check(tool):
    t = it.TOOLS[tool]
    tpl = load(t.template)
    assert check_template(t.template, info())["ok"], t.template
    req = tpl.manifest["requires"]
    assert req["pack"] and req["nodes"] and (req.get("assumed") or req.get("verified"))
    assert t.template in TIMEOUTS
    inputs = {"image": "a.png", "mask": "m.png", "garment": "g.png", "words": "men", "width": 640, "height": 480,
              "seed": 3}
    graph, resolved = build(t.template, inputs)
    titles = {n["_meta"]["title"]: n for n in graph.values()}
    assert titles["Save"]["inputs"]["filename_prefix"] == "mixai/tool"
    assert resolved["width"] == 640


def test_missing_pack_is_reported():
    broken = info()
    for cls in ("LoadAndApplyICLightUnet", "CatVTON"):
        del broken[cls]
    broken["INPAINT_LoadInpaintModel"]["input"]["required"]["model_name"][1]["options"] = []
    assert check_template("image_iclight", broken)["missing_nodes"] == ["LoadAndApplyICLightUnet"]
    assert check_template("image_catvton", broken)["missing_nodes"] == ["CatVTON"]
    assert check_template("image_lama_inpaint", broken)["missing_models"] == ["big-lama.pt"]
    assert check_template("image_depth", broken)["ok"]


def test_iclight_background_branch():
    g, _ = build("image_iclight", {"image": "a.png", "prompt": "x"})
    cond = next(n for n in g.values() if n["_meta"]["title"] == "Condition")
    assert "opt_background" not in cond["inputs"]
    assert not any(n["_meta"]["title"] == "Background" for n in g.values())
    g, _ = build("image_iclight", {"image": "a.png", "background": "b.png", "iclight_model": "iclight_sd15_fbc.safetensors"})
    cond = next(n for n in g.values() if n["_meta"]["title"] == "Condition")
    assert "opt_background" in cond["inputs"]
    assert next(n for n in g.values() if n["_meta"]["title"] == "ICLight")["inputs"]["model_path"].endswith("fbc.safetensors")


def test_depth_is_pinned_to_the_apache_model():
    g, _ = build("image_depth", {"image": "a.png"})
    assert next(n for n in g.values() if n["_meta"]["title"] == "Detect")["inputs"]["ckpt_name"] == "depth_anything_v2_vits.pth"


# ------------------------------------------------------------------ driver plans

def test_plan_dispatch_for_each_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    src = tmp_path / "src.png"
    src.write_bytes(png_bytes())
    (tmp_path / "m.png").write_bytes(png_bytes())

    class Lookup:
        def generation_file(self, gid):
            return src

    for tool in it.TOOLS.values():
        rec = {"tool": tool.id, "template": tool.template, "source_id": "g1",
               "inputs": {"width": 512, "height": 512}}
        if tool.needs_mask:
            rec["mask_file"] = "m.png"
        if tool.id == "tryon":
            rec["garment_id"] = "g2"
        if tool.id == "remove":
            rec["inputs"]["words"] = "men"
        plan =plan_generation("image", "x", {"image_tool": rec, "width": 512, "height": 512}, 7, Lookup())
        assert plan.template == tool.template and plan.images["image"] == src
        assert plan.inputs["seed"] == 7 and plan.sources["tool"] == tool.id
        if tool.needs_mask:
            assert plan.images["mask"] == tmp_path / "m.png"
        if tool.id == "tryon":
            assert plan.images["garment"] == src and plan.sources["garment_id"] == "g2"
        build(plan.template, {**plan.inputs, **{k: "f.png" for k in plan.images}})

    with pytest.raises(Exception, match="unknown image tool template"):
        plan_generation("image", "x", {"image_tool": {"template": "qwen_edit", "source_id": "g1"}}, 1, Lookup())


# ------------------------------------------------------------------ helpers

def test_slot_values_and_prompts():
    scaffold = "Keep image 1 and place them in [new background]. Relight: [lighting], soft."
    text = "Keep image 1 and place them in a Lisbon rooftop. Relight: golden hour, soft."
    slots = it.slot_values(scaffold, text)
    assert slots == {"new background": "a Lisbon rooftop", "lighting": "golden hour"}
    assert it.slot_values(scaffold, "something else entirely") == {}
    tpl = {"tool_prompt": "a person in [new background], [lighting]"}
    assert it.relight_prompt(tpl, slots, text) == "a person in a Lisbon rooftop, golden hour"
    assert it.relight_prompt(tpl, {}, "  make  it\nsunset ") == "make it sunset"
    assert it.cloth_of({}, {"garment type": "lower-body"}) == "lower"
    assert it.cloth_of({}, {"garment type": "full-length"}) == "overall"
    assert it.cloth_of({"garment_type": "upper"}, {}) == "upper"
    with pytest.raises(it.ToolError):
        it.cloth_of({"garment_type": "hat"}, {})
    assert it.light_of("left") == "Left Light" and it.light_of(None) is None
    with pytest.raises(it.ToolError):
        it.light_of("sideways")


def test_work_sizes():
    assert it.work_size(it.TOOLS["depth"], 320, 200) == (320, 200)  # never upscaled
    w, h = it.work_size(it.TOOLS["inpaint"], 6000, 4000)
    assert w % 8 == 0 and h % 8 == 0 and w * h <= 4.2e6
    w, h = it.work_size(it.TOOLS["relight"], 320, 200)  # SD1.5 works at its own size, up or down
    assert w % 64 == 0 and h % 64 == 0 and 0.6e6 < w * h <= 0.8e6


# ------------------------------------------------------------------ the route

def test_tools_endpoint_and_gating(client, nc_off, monkeypatch):
    rows = {t["id"]: t for t in client.get("/api/images/tools").json()["tools"]}
    assert {"depth", "canny", "inpaint", "relight"} <= set(rows)
    assert not {"tryon", "lineart", "lineart_anime", "softedge"} & set(rows)
    assert rows["inpaint"]["licence"].startswith("Apache-2.0") and rows["inpaint"]["available"]
    monkeypatch.setattr(get_settings(), "image_allow_noncommercial", True)
    rows = {t["id"]: t for t in client.get("/api/images/tools").json()["tools"]}
    assert rows["tryon"]["noncommercial"] and "CC BY-NC-SA" in rows["tryon"]["licence"]


def test_template_cards_follow_the_switch(client, monkeypatch):
    rows = {t["id"]: t for t in client.get("/api/prompt-templates", params={"type": "image"}).json()}
    tryon = rows["image-edit-virtual-tryon"]
    assert tryon["tool"]["id"] == "tryon" and tryon["tool"]["noncommercial"] and not tryon["tool"]["fallback"]
    assert tryon["ref_labels"] == ["Person", "Garment"] and tryon["refs_required"] == 2
    assert rows["image-edit-outfit-swap"]["tool"] is None  # the commercial-safe one stays plain Qwen
    assert rows["image-edit-remove-object"]["tool"]["needs_mask"]
    assert rows["image-edit-line-art"]["title"] == "Photo to line art / colouring page"
    start = client.post("/api/prompt-templates/image-edit-relight/start").json()
    assert start["prefill"]["tool"]["id"] == "relight"

    monkeypatch.setattr(get_settings(), "image_allow_noncommercial", False)
    rows = {t["id"]: t for t in client.get("/api/prompt-templates", params={"type": "image"}).json()}
    assert "image-edit-virtual-tryon" not in rows
    assert {"image-edit-line-art", "image-edit-depth-map", "image-edit-edge-map"} <= set(rows)
    assert client.post("/api/prompt-templates/image-edit-virtual-tryon/start").status_code == 404


def test_line_art_runs_the_tool_or_falls_back(client, photo, monkeypatch):
    text = ("Turn image 1 into a manga-style ink drawing with confident strokes: clean black outlines on pure white, "
            "no shading, no grey fills and no colour, closed shapes, keeping the composition and every main subject "
            "recognisable.")
    r = edit(client, source_ids=[photo["id"]], instruction=text, template_id="image-edit-line-art", count=2)
    assert r.status_code == 202, r.text
    assert r.json()["tool_resolved"] == "lineart_anime" and len(r.json()["items"]) == 1  # deterministic: one
    p = gen_params(r.json()["items"][0])
    assert p["image_tool"]["template"] == "image_lineart_anime" and p["template_id"] == "image-edit-line-art"
    assert p["image_tool"]["inputs"]["width"] == 320 and "reference_ids" not in p

    monkeypatch.setattr(get_settings(), "image_allow_noncommercial", False)
    r = edit(client, source_ids=[photo["id"]], instruction=text, template_id="image-edit-line-art")
    assert r.status_code == 202 and r.json()["tool_resolved"] is None
    assert "isn't available on this server" in r.json()["tool_fallback"]
    assert "NONCOMMERCIAL" not in r.json()["tool_fallback"]
    p = gen_params(r.json()["items"][0])
    assert "image_tool" not in p and p["model"] == "qwen_image_edit_2511" and p["tool_fallback"]
    r = edit(client, source_ids=[photo["id"]], instruction=text, template_id="image-edit-line-art", engine="tool")
    assert r.status_code == 422 and "isn't available" in r.json()["detail"]


def test_tool_only_cards_refuse_instead_of_falling_back(client, photo, nc_off):
    r = edit(client, source_ids=[photo["id"], photo["id"]], instruction="x", template_id="image-edit-virtual-tryon")
    assert r.status_code == 422 and "isn't available" in r.json()["detail"]
    r = edit(client, source_ids=[photo["id"]], instruction="x", tool="tryon")
    assert r.status_code == 422
    # depth needs no instruction at all
    r = edit(client, source_ids=[photo["id"]], template_id="image-edit-depth-map")
    assert r.status_code == 202 and r.json()["tool_resolved"] == "depth"
    # soft edges are Annotators weights: switched off, the edge card keeps working with Canny
    r = edit(client, source_ids=[photo["id"]], instruction="Trace image 1 as soft HED edges, white on black.",
             template_id="image-edit-edge-map")
    assert r.json()["tool_resolved"] == "canny"
    assert edit(client, source_ids=[photo["id"]], instruction="", template_id="image-edit-line-art").status_code == 422
    assert edit(client, source_ids=[photo["id"]], instruction="x", template_id="image-portrait").status_code == 422


def test_unavailable_tool_falls_back_or_refuses(client, photo, monkeypatch):
    broken = info()
    del broken["LoadAndApplyICLightUnet"], broken["DepthAnythingV2Preprocessor"]
    monkeypatch.setattr(get_settings(), "gen_driver", "comfy")
    monkeypatch.setattr(mc, "_cached_info", lambda: broken)
    assert "LoadAndApplyICLightUnet" in it.unavailable(it.TOOLS["relight"])
    r = edit(client, source_ids=[photo["id"]], instruction="relight it warm", template_id="image-edit-relight")
    assert r.status_code == 202 and "missing nodes" in r.json()["tool_fallback"]
    r = edit(client, source_ids=[photo["id"]], template_id="image-edit-depth-map")
    assert r.status_code == 422 and "Depth map can't run" in r.json()["detail"]
    rows = {t["id"]: t for t in it.options(broken, driver="comfy")}
    assert rows["relight"]["available"] is False and rows["inpaint"]["available"] is True
    assert all(not t["available"] for t in it.options(None, driver="comfy", error="down"))


def test_relight_and_tryon_params(client, photo):
    text = ("Keep the subject of image 1 exactly as they are, including face, body, clothing and pose, and place them "
            "in a rooftop at dusk. Relight them to match: neon city night, magenta and cyan edge light, with "
            "consistent shadows, colour spill and a contact shadow so they sit naturally in the scene.")
    r = edit(client, source_ids=[photo["id"], photo["id"]], instruction=text, template_id="image-edit-relight",
             tool_options={"light": "right"}, count=2, seed=5)
    assert r.status_code == 202, r.text
    assert len(r.json()["items"]) == 2  # sampled: count is kept
    t = gen_params(r.json()["items"][0])["image_tool"]
    assert t["inputs"]["light"] == "Right Light" and t["inputs"]["denoise"] == 0.9
    assert t["inputs"]["prompt"].startswith("a person in a rooftop at dusk, neon city night")
    assert t["inputs"]["iclight_model"] == "iclight_sd15_fbc.safetensors" and t["background_id"]
    assert edit(client, source_ids=[photo["id"]], instruction=text, template_id="image-edit-relight",
                tool_options={"light": "sideways"}).status_code == 422

    text = "Dress the person in image 1 in the lower-body garment from image 2, keeping their face, pose and background."
    r = edit(client, source_ids=[photo["id"], photo["id"]], instruction=text, template_id="image-edit-virtual-tryon")
    assert r.status_code == 202, r.text
    t = gen_params(r.json()["items"][0])["image_tool"]
    assert t["inputs"]["cloth_type"] == "lower" and t["garment_id"] and t["template"] == "image_catvton"
    r = edit(client, source_ids=[photo["id"]], instruction=text, template_id="image-edit-virtual-tryon")
    assert r.status_code == 422 and "garment" in r.json()["detail"]


# ------------------------------------------------------------------ LaMa and masks

def test_inpaint_without_mask_uses_qwen(client, photo):
    r = edit(client, source_ids=[photo["id"]], instruction="Remove the bin from image 1",
             template_id="image-edit-remove-object")
    assert r.status_code == 202 and "no mask" in r.json()["tool_fallback"]
    r = edit(client, source_ids=[photo["id"]], instruction="Remove the bin", template_id="image-edit-remove-object",
             engine="tool")
    assert r.status_code == 422 and "no mask" in r.json()["detail"]


def test_inpaint_mask_validation(client, photo):
    body = {"source_ids": [photo["id"]], "template_id": "image-edit-remove-object"}
    assert edit(client, **body, mask="not a data url").status_code == 422
    assert "readable" in edit(client, **body, mask="data:image/png;base64,AAAA").json()["detail"]
    empty = mask_url(box=(0, 0, 0, 0))
    assert "empty" in edit(client, **body, mask=empty).json()["detail"]
    full = mask_url(box=(0, 0, 320, 200))
    assert "whole picture" in edit(client, **body, mask=full).json()["detail"]

    # a brush layer at display size: scaled to the photo, thresholded, stored under the workspace
    r = edit(client, **body, mask=mask_url(size=(640, 400), box=(200, 120, 360, 280)), count=3)
    assert r.status_code == 202, r.text
    assert len(r.json()["items"]) == 1 and r.json()["tool_resolved"] == "inpaint"
    t = gen_params(r.json()["items"][0])["image_tool"]
    assert t["mask_from"] == "brush" and t["template"] == "image_lama_inpaint"
    m = Image.open(get_settings().data_dir / t["mask_file"])
    assert m.size == (320, 200) and set(m.convert("L").getdata()) == {0, 255}
    assert m.convert("L").getpixel((140, 100)) == 255 and m.convert("L").getpixel((10, 10)) == 0


def test_inpaint_alpha_brush(client, photo):
    layer = Image.new("RGBA", (320, 200), (0, 0, 0, 0))
    layer.paste((255, 0, 0, 255), (50, 50, 90, 90))
    buf = io.BytesIO()
    layer.save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    r = edit(client, source_ids=[photo["id"]], template_id="image-edit-remove-object", mask=url)
    assert r.status_code == 202, r.text
    t = gen_params(r.json()["items"][0])["image_tool"]
    m = Image.open(get_settings().data_dir / t["mask_file"]).convert("L")
    assert m.getpixel((70, 70)) == 255 and m.getpixel((200, 150)) == 0


def test_inpaint_uses_a_photo_selection(client, photo, fast_driver):
    # a Photo Studio Select version: its uncut base is edited, its selection is the mask
    with SessionLocal() as db:
        g = db.get(Generation, photo["generation_id"])
        rel = f"workspaces/{g.workspace_id}/media/sel.mask.png"
        sel = Image.new("L", (320, 200), 0)
        sel.paste(255, (20, 20, 80, 80))
        (get_settings().data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        sel.save(get_settings().data_dir / rel)
        g.params = {**(g.params or {}), "cutout": {"base_id": g.id, "mask_file": rel}}
        db.commit()
    r = edit(client, source_ids=[photo["id"]], template_id="image-edit-remove-object")
    assert r.status_code == 202, r.text
    t = gen_params(r.json()["items"][0])["image_tool"]
    assert t["mask_from"] == "selection" and t["source_id"] == photo["generation_id"]
    _run_all(fast_driver)
    out = client.get(f"/api/media/{r.json()['items'][0]['id']}").json()
    assert out["status"] == "ready"
