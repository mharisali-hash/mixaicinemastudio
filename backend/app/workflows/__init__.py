"""ComfyUI workflow templates (API format) plus the manifest that says how to fill them.

Each template lives in its own folder: workflow.json (graph) + manifest.json.
Manifests only ever address nodes by `_meta.title`, so a graph can be re-exported
from the ComfyUI editor (new node ids) without touching any Python.
"""
import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

TEMPLATE_DIR = Path(__file__).resolve().parent

# loader inputs whose enum is a list of files on the GPU box
MODEL_FIELDS = {
    "unet_name", "clip_name", "clip_name1", "clip_name2", "vae_name", "ckpt_name",
    "lora_name", "text_encoder", "model_name", "upscale_model_name", "bg_removal_name", "checkpoint",
    "name",
}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Template:
    name: str
    graph: dict
    manifest: dict

    def node_id(self, title: str) -> str:
        return find_node(self.graph, title)


def find_node(graph: dict, title: str) -> str:
    for nid, node in graph.items():
        if node.get("_meta", {}).get("title") == title:
            return nid
    raise TemplateError(f"no node titled '{title}'")


def list_templates() -> list[str]:
    return sorted(p.parent.name for p in TEMPLATE_DIR.glob("*/manifest.json"))


@lru_cache
def _load_raw(name: str) -> Template:
    folder = TEMPLATE_DIR / name
    if not (folder / "manifest.json").is_file():
        raise TemplateError(f"unknown template '{name}'")
    graph = json.loads((folder / "workflow.json").read_text(encoding="utf-8"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    titles = [n.get("_meta", {}).get("title") for n in graph.values()]
    dupes = {t for t in titles if t and titles.count(t) > 1}
    if dupes:
        raise TemplateError(f"{name}: duplicate node titles {sorted(dupes)}")
    return Template(name, graph, manifest)


def load(name: str) -> Template:
    t = _load_raw(name)
    return Template(t.name, copy.deepcopy(t.graph), copy.deepcopy(t.manifest))


def snap_8n1(frames: int | float) -> int:
    """LTX wants 8n+1 frames; round to the nearest valid count (min 9)."""
    n = max(1, round((float(frames) - 1) / 8))
    return 8 * n + 1


def frames_for(seconds: float, fps: float) -> int:
    return snap_8n1(seconds * fps + 1)


def snap_multiple(value: int | float, m: int) -> int:
    return max(m, int(round(float(value) / m)) * m)


def _is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)


def _coerce(name: str, spec: dict, value: Any) -> Any:
    kind = spec.get("type", "str")
    try:
        if kind == "int":
            value = int(round(float(value)))
            if spec.get("frames_8n1"):
                value = snap_8n1(value)
            if spec.get("multiple_of"):
                value = snap_multiple(value, int(spec["multiple_of"]))
            return value
        if kind == "float":
            return float(value)
    except (TypeError, ValueError):
        raise TemplateError(f"input '{name}' must be a number, got {value!r}") from None
    if kind in ("str", "image"):
        return str(value)
    if kind == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    return value


def remove_nodes(graph: dict, titles: Iterable[str], bypass: dict[str, dict] | None = None) -> None:
    """Drop nodes by title. Consumers either get rewired through the dropped node
    (bypass maps output index -> the dropped node's input that carries the same thing)
    or simply lose that input, which is right for optional sockets like image2/image3."""
    bypass = bypass or {}
    doomed: dict[str, dict] = {}
    for title in titles:
        nid = find_node(graph, title)
        doomed[nid] = {int(k): v for k, v in bypass.get(title, {}).items()}

    def resolve(link):
        # follow chains of bypassed nodes (A -> dropped B -> dropped C)
        seen = 0
        while _is_link(link) and link[0] in doomed:
            rewire = doomed[link[0]].get(link[1])
            if rewire is None:
                return None
            link = graph[link[0]]["inputs"].get(rewire)
            seen += 1
            if seen > 50:
                raise TemplateError("bypass loop")
        return link

    for nid, node in graph.items():
        if nid in doomed:
            continue
        for key, val in list(node["inputs"].items()):
            if _is_link(val) and val[0] in doomed:
                new = resolve(val)
                if new is None:
                    del node["inputs"][key]
                else:
                    node["inputs"][key] = new
    for nid in doomed:
        del graph[nid]


def insert_loras(graph: dict, after: str, loras: list[tuple[str, float]], output: int = 0) -> None:
    if not loras:
        return
    src = find_node(graph, after)
    consumers = [
        (node, key) for nid, node in graph.items() for key, val in node["inputs"].items()
        if _is_link(val) and val[0] == src and val[1] == output
    ]
    next_id = max(int(k) for k in graph if k.isdigit()) + 1
    prev = [src, output]
    for i, (lora_name, strength) in enumerate(loras, 1):
        nid = str(next_id)
        next_id += 1
        graph[nid] = {
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": f"LoRA {i}"},
            "inputs": {"model": prev, "lora_name": lora_name, "strength_model": float(strength)},
        }
        prev = [nid, 0]
    for node, key in consumers:
        node["inputs"][key] = list(prev)


def batch_latent(graph: dict, size: int, index: int, length: int) -> None:
    """Draw pictures index..index+length-1 of a batch of `size` from the graph's empty latent.

    LatentFromBatch tags the latent with batch_index, and with that tag ComfyUI draws each picture's noise
    on its own, in index order, from the one seed. So picture 5 of a batch of 8 comes out the same when it is
    drawn alone later (size 6, index 5, length 1): only a few empty latents are allocated, one is sampled."""
    lat = find_node(graph, "Latent")
    if "batch_size" not in graph[lat]["inputs"]:
        raise TemplateError("this graph's latent has no batch size")
    graph[lat]["inputs"]["batch_size"] = int(size)
    consumers = [(node, key) for node in graph.values() for key, val in node["inputs"].items()
                 if _is_link(val) and val[0] == lat]
    nid = str(max(int(k) for k in graph if k.isdigit()) + 1)
    graph[nid] = {"class_type": "LatentFromBatch", "_meta": {"title": "Batch pick"},
                  "inputs": {"samples": [lat, 0], "batch_index": int(index), "length": int(length)}}
    for node, key in consumers:
        node["inputs"][key] = [nid, 0]


def _check_links(graph: dict) -> None:
    for nid, node in graph.items():
        for key, val in node["inputs"].items():
            if _is_link(val) and val[0] not in graph:
                title = node.get("_meta", {}).get("title", nid)
                raise TemplateError(f"{title}.{key} points at a node that was removed")


def build(name: str, inputs: dict, loras: list[tuple[str, float]] | None = None) -> tuple[dict, dict]:
    """Return (api_graph, resolved_inputs). Unknown keys in `inputs` are ignored on purpose:
    the driver passes generation params straight through."""
    tpl = load(name)
    graph, spec_all = tpl.graph, tpl.manifest["inputs"]
    resolved: dict[str, Any] = {}
    drop: list[str] = []
    bypass: dict[str, dict] = {}

    for key, spec in spec_all.items():
        value = inputs.get(key)
        if spec.get("type") == "image_list":
            images = [str(v) for v in (value or []) if v]
            slots = spec["slots"]
            if len(images) < spec.get("min", 0):
                raise TemplateError(f"{name}: '{key}' needs at least {spec.get('min')} image(s)")
            if len(images) > len(slots):
                raise TemplateError(f"{name}: '{key}' takes at most {len(slots)} images")
            for (title, field), img in zip(slots, images):
                graph[find_node(graph, title)]["inputs"][field] = img
            # chained slots (ReferenceLatent -> ReferenceLatent) need rewiring, not just dropping
            rewire = spec.get("bypass_unused", {})
            for group in spec.get("drop_unused", [])[max(0, len(images) - 1):]:
                drop.extend(group)
                bypass.update({t: rewire[t] for t in group if t in rewire})
            resolved[key] = images
            continue

        if "switch" in spec:
            # ComfySwitchNode resolved here rather than on the server: the unused branch is cut off and
            # pruned, so its weights never load
            on = bool(spec.get("default") if value is None else value)
            for title in spec["switch"]:
                bypass[title] = {0: "on_true" if on else "on_false"}
            resolved[key] = on
            continue

        if "if_set" in spec:
            # a switch that removes part of the graph (ltx23_ia2v's single_stage drops the second pass)
            on = bool(spec.get("default") if value is None else value)
            if on:
                drop.extend(spec["if_set"].get("drop", []))
                bypass.update(spec["if_set"].get("bypass", {}))
            resolved[key] = on
            continue

        if value is None or value == "":
            if spec.get("required"):
                raise TemplateError(f"{name}: missing required input '{key}'")
            if "if_missing" in spec:
                drop.extend(spec["if_missing"].get("drop", []))
                bypass.update(spec["if_missing"].get("bypass", {}))
                resolved[key] = None
                continue
            if "default" not in spec:
                continue
            value = spec["default"]

        value = _coerce(key, spec, value)
        for title, field in spec.get("targets", []):
            graph[find_node(graph, title)]["inputs"][field] = value
        resolved[key] = value

    if drop or bypass:
        remove_nodes(graph, list(dict.fromkeys(drop + list(bypass))), bypass)

    if tpl.manifest.get("prune"):
        graph = prune(graph, [spec["node"] for spec in tpl.manifest.get("outputs", {}).values()])

    if loras:
        point = tpl.manifest.get("lora")
        if not point:
            raise TemplateError(f"{name} has no LoRA insertion point")
        insert_loras(graph, point["after"], loras, point.get("output", 0))
        resolved["loras"] = [{"name": n, "strength": s} for n, s in loras]

    _check_links(graph)
    return graph, resolved


def prune(graph: dict, output_titles: Iterable[str]) -> dict:
    """Only the nodes the outputs depend on (a resolved switch leaves the other branch hanging)."""
    stack = [find_node(graph, t) for t in output_titles]
    keep: set[str] = set()
    while stack:
        nid = stack.pop()
        if nid in keep:
            continue
        keep.add(nid)
        stack += [v[0] for v in graph[nid]["inputs"].values() if _is_link(v) and v[0] in graph]
    return {nid: node for nid, node in graph.items() if nid in keep}


def output_titles(name: str) -> dict[str, dict]:
    return _load_raw(name).manifest.get("outputs", {})


def _enum_options(spec: list) -> list | None:
    if not spec:
        return None
    head = spec[0]
    if isinstance(head, list):
        return head
    opts = spec[1].get("options") if len(spec) > 1 and isinstance(spec[1], dict) else None
    if head == "COMBO" and isinstance(opts, list):
        return opts
    if head == "COMFY_DYNAMICCOMBO_V3" and isinstance(opts, list):
        return [o.get("key") for o in opts if isinstance(o, dict)]
    return None


def check_template(name: str, object_info: dict) -> dict:
    """Compare a template against a server's /object_info. Runtime-filled inputs
    (prompt, uploaded image names, sizes) are skipped; everything literal is checked."""
    tpl = _load_raw(name)
    # nodes that only serve an optional feature (e.g. smooth motion) don't decide the template's ok
    feature_of = {t: f for f, titles in tpl.manifest.get("features", {}).items() for t in titles}
    feature_problems: dict[str, list[str]] = {f: [] for f in tpl.manifest.get("features", {})}
    runtime = {
        (t, f)
        for spec in tpl.manifest["inputs"].values()
        for t, f in spec.get("targets", []) + spec.get("slots", [])
    }
    # switch nodes never reach ComfyUI (build() resolves them)
    switches = {t for spec in tpl.manifest["inputs"].values() for t in spec.get("switch", [])}
    missing_nodes: set[str] = set()
    missing_models: set[str] = set()
    invalid: list[str] = []

    for nid, node in tpl.graph.items():
        cls = node["class_type"]
        title = node.get("_meta", {}).get("title", nid)
        if title in switches:
            continue
        info = object_info.get(cls)
        if title in feature_of:
            feature_problems[feature_of[title]] += _node_problems(title, node, info, runtime)
            continue
        if info is None:
            missing_nodes.add(cls)
            continue
        req = info.get("input", {}).get("required", {}) or {}
        opt = info.get("input", {}).get("optional", {}) or {}
        for key in req:
            if key not in node["inputs"] and not _may_be_empty(req[key], key, node["inputs"]):
                invalid.append(f"{title}.{key}: required input not set")
        for key, val in node["inputs"].items():
            if _is_link(val) or (title, key) in runtime:
                continue
            spec = req.get(key) or opt.get(key)
            if spec is None:
                continue  # extra widget values (e.g. VHS per-format options) are ignored by Comfy
            options = _enum_options(spec)
            if options is None or val in options:
                continue
            if key in MODEL_FIELDS:
                missing_models.add(str(val))
            else:
                invalid.append(f"{title}.{key}: {val!r} not in {options[:8]}{'…' if len(options) > 8 else ''}")

    warnings = []
    lora_opts = _enum_options(
        object_info.get("LoraLoaderModelOnly", {}).get("input", {}).get("required", {}).get("lora_name", [])
    ) or []
    for f in tpl.manifest.get("optional_models", {}).get("loras", []):
        if f not in lora_opts:
            warnings.append(f"optional LoRA missing: {f}")

    out = {
        "ok": not (missing_nodes or missing_models or invalid),
        "missing_nodes": sorted(missing_nodes),
        "missing_models": sorted(missing_models),
        "invalid": invalid,
        "warnings": warnings,
    }
    if feature_problems:
        out["features"] = {f: {"ok": not probs, "problems": probs} for f, probs in feature_problems.items()}
    return out


def _may_be_empty(spec, key: str, inputs: dict) -> bool:
    # a V3 autogrow list (Qwen-Image 2.1's reference images) is listed as required, but with min 0 the
    # prompt may leave it out and ComfyUI hands the node {}; filled slots arrive as "images.image_1"
    if not (isinstance(spec, list) and spec and spec[0] == "COMFY_AUTOGROW_V3"):
        return False
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    return (opts.get("template") or {}).get("min", 0) == 0 or any(k.startswith(f"{key}.") for k in inputs)


def _node_problems(title: str, node: dict, info: dict | None, runtime: set) -> list[str]:
    if info is None:
        return [f"missing node {node['class_type']}"]
    req = info.get("input", {}).get("required", {}) or {}
    opt = info.get("input", {}).get("optional", {}) or {}
    probs = [f"{title}.{k}: required input not set" for k in req
             if k not in node["inputs"] and not _may_be_empty(req[k], k, node["inputs"])]
    for key, val in node["inputs"].items():
        if _is_link(val) or (title, key) in runtime:
            continue
        options = _enum_options(req.get(key) or opt.get(key) or [])
        if options is not None and val not in options:
            probs.append(f"missing model {val}" if key in MODEL_FIELDS else f"{title}.{key}: {val!r} not allowed")
    return probs
