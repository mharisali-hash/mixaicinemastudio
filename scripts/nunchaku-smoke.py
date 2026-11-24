#!/usr/bin/env python3
"""One INT4 edit straight through ComfyUI, run twice (cold, then warm), to prove the Nunchaku install works
before the app uses it. Standard library only; reads the app's own qwen_edit_int4 graph.

    python3 scripts/nunchaku-smoke.py [--url http://127.0.0.1:8188] [--image some.png] [--refs 1-3] [--free]
    python3 scripts/nunchaku-smoke.py --workflow qwen_edit --free   # today's fp8 model, same input, for comparison
"""
import argparse
import json
import struct
import sys
import time
import urllib.request
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def post(url, data: bytes, ctype: str):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def test_png(w=768, h=1024) -> bytes:
    # a soft gradient with a darker block: enough for the edit to have something to change
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            block = 260 < x < 520 and 380 < y < 760
            row += bytes((90 if block else 120 + x * 100 // w, 70 if block else 150, 60 if block else 200 - y * 80 // h))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def upload(base, data: bytes, name: str) -> str:
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            "Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{b}\r\nContent-Disposition: form-data; " \
        f"name=\"overwrite\"\r\n\r\ntrue\r\n--{b}--\r\n".encode()
    return post(f"{base}/upload/image", body, f"multipart/form-data; boundary={b}")["name"]


def render(base, graph, seed) -> float:
    g = json.loads(json.dumps(graph))
    g["31"]["inputs"]["seed"] = seed
    t0 = time.monotonic()
    pid = post(f"{base}/prompt", json.dumps({"prompt": g, "client_id": "nunchaku-smoke"}).encode(),
               "application/json")["prompt_id"]
    while True:
        time.sleep(1)
        h = get(f"{base}/history/{pid}").get(pid)
        if not h:
            if time.monotonic() - t0 > 900:
                raise RuntimeError("no result after 15 min")
            continue
        st = h.get("status", {})
        if st.get("status_str") == "error":
            msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
            raise RuntimeError(json.dumps(msgs[-1][1] if msgs else st)[:1500])
        if h.get("outputs"):
            return time.monotonic() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--image", help="a picture to edit (default: a generated test card)")
    ap.add_argument("--workflow", default="qwen_edit_int4", choices=("qwen_edit_int4", "qwen_edit"),
                    help="qwen_edit = today's fp8 model, for a side-by-side on the same input")
    ap.add_argument("--refs", type=int, default=1, choices=(1, 2, 3),
                    help="how many reference pictures (bulk items mostly use 1)")
    ap.add_argument("--offload", choices=("auto", "enable", "disable"), help="override the INT4 loader's cpu_offload")
    ap.add_argument("--free", action="store_true", help="unload every model on this ComfyUI first (fair timing)")
    a = ap.parse_args()
    base = a.url.rstrip("/")
    int4 = a.workflow == "qwen_edit_int4"
    graph = json.loads((ROOT / "backend" / "app" / "workflows" / a.workflow / "workflow.json").read_text(encoding="utf-8-sig"))

    if int4:
        loader = get(f"{base}/object_info").get("NunchakuQwenImageDiTLoader")
        if loader is None:
            print("xx ComfyUI has no NunchakuQwenImageDiTLoader: the node pack didn't load. See: "
                  "sudo journalctl -u 'comfyui*' -n 200 --no-pager | grep -iA8 nunchaku")
            return 1
        have = set(loader["input"].get("required", {})) | set(loader["input"].get("optional", {}))
        if set(graph["1"]["inputs"]) - have:
            print(f"xx the loader takes {sorted(have)}, our graph sends {sorted(graph['1']['inputs'])}: send me this line")
            return 1
        if a.offload:
            graph["1"]["inputs"]["cpu_offload"] = a.offload

    # same trimming the app does when a job has fewer pictures
    for k in range(a.refs + 1, 4):
        graph.pop(str(10 + k)); graph.pop(str(13 + k))
        for n in ("20", "21"):
            graph[n]["inputs"].pop(f"image{k}", None)

    if a.free:
        # /free answers with an empty body, so not post()
        urllib.request.urlopen(urllib.request.Request(f"{base}/free", data=b'{"unload_models": true, "free_memory": true}',
                                                      headers={"Content-Type": "application/json"}), timeout=60).read()
        time.sleep(3)
    vram = get(f"{base}/system_stats")["devices"][0]
    print(f"    {a.workflow}, {a.refs} picture(s); card free before the run: {vram['vram_free'] / 2**30:.1f} of "
          f"{vram['vram_total'] / 2**30:.1f} GB" + (f", cpu_offload={graph['1']['inputs']['cpu_offload']}" if int4 else ""))
    data = Path(a.image).read_bytes() if a.image else test_png()
    name = upload(base, data, "nunchaku-smoke.png")
    for n in ("11", "12", "13"):
        if n in graph:
            graph[n]["inputs"]["image"] = name
    graph["20"]["inputs"]["prompt"] = "Make the dark rectangle a bright red door; keep everything else the same."
    graph["30"]["inputs"].update(width=768, height=1024)
    graph["33"]["inputs"]["filename_prefix"] = f"mixai/smoke_{a.workflow}"
    # warm runs only re-sample and decode (the encoded prompt is cached), so the 3rd is the steady-state figure
    for i, label in enumerate(("cold (loads the model)", "warm", "warm")):
        try:
            secs = render(base, graph, 1234 + i)
        except Exception as e:  # noqa: BLE001 - the whole point is to print it
            print(f"xx {label}: {e}")
            return 1
        print(f"    {label}: {secs:.1f} s")
    print(f"    output is in ComfyUI/output/mixai/smoke_{a.workflow}_*.png - have a look at it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
