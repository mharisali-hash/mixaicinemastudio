#!/usr/bin/env python3
"""One INT4 edit straight through ComfyUI, run twice (cold, then warm), to prove the Nunchaku install works
before the app uses it. Standard library only; reads the app's own qwen_edit_int4 graph.

    python3 scripts/nunchaku-smoke.py [--url http://127.0.0.1:8188] [--image some.png]
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
GRAPH = ROOT / "backend" / "app" / "workflows" / "qwen_edit_int4" / "workflow.json"


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
    a = ap.parse_args()
    base = a.url.rstrip("/")

    info = get(f"{base}/object_info")
    loader = info.get("NunchakuQwenImageDiTLoader")
    if loader is None:
        print("xx ComfyUI has no NunchakuQwenImageDiTLoader: the node pack didn't load. See: "
              "sudo journalctl -u 'comfyui*' -n 200 --no-pager | grep -iA8 nunchaku")
        return 1
    print("    loader inputs:", json.dumps({k: (v[0] if isinstance(v[0], str) else "list")
                                            for k, v in loader["input"].get("required", {}).items()}))

    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    want = set(graph["1"]["inputs"])
    have = set(loader["input"].get("required", {})) | set(loader["input"].get("optional", {}))
    if want - have:
        print(f"xx the loader takes {sorted(have)}, our graph sends {sorted(want)}: send me this line")
        return 1
    data = Path(a.image).read_bytes() if a.image else test_png()
    name = upload(base, data, "nunchaku-smoke.png")
    for n in ("11", "12", "13"):
        graph[n]["inputs"]["image"] = name
    graph["20"]["inputs"]["prompt"] = "Make the dark rectangle a bright red door; keep everything else the same."
    graph["30"]["inputs"].update(width=768, height=1024)
    for i, label in enumerate(("cold (loads the model)", "warm")):
        try:
            s = render(base, graph, 1234 + i)
        except Exception as e:  # noqa: BLE001 - the whole point is to print it
            print(f"xx {label}: {e}")
            return 1
        print(f"    {label}: {s:.1f} s")
    print("    output is in ComfyUI/output/mixai/qwenedit_int4_*.png - have a look at it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
