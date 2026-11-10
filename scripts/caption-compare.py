"""Caption the same pictures three ways, side by side: the app's vision model, Qwen2.5-VL and JoyCaption.

    cd backend && uv run python ../scripts/caption-compare.py [--n 10] [--ids <media id>,...] [--html out.html]

Read-only for the app: it reads finished pictures from the Library and changes nothing. JoyCaption runs in the
image GPU's ComfyUI (Image Batch Path -> JoyCaption -> Caption Saver, one picture at a time); the first run loads
the model, and downloads it if it isn't there yet, so give it a few minutes.
"""
import argparse
import base64
import html
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Generation, MediaItem  # noqa: E402
from app.services import generation_file  # noqa: E402

ASK = ("Describe this picture in plain sentences. For every person say whether it is a man, a woman, a boy or a "
       "girl, roughly how old, and what they wear. Name any animals exactly (which kind). List the main objects, "
       "the setting and the light. Don't guess at names.")


def pictures(n: int, ids: list[str]) -> list[tuple[str, Path]]:
    out = []
    with SessionLocal() as db:
        # only the columns this needs, so it runs on any schema version
        q = select(MediaItem.id, MediaItem.title, MediaItem.generation_id).where(MediaItem.kind == "image")
        q = q.where(MediaItem.id.in_(ids)) if ids else q.order_by(MediaItem.created_at.desc())
        for mid, title, gid in db.execute(q.limit(400)):
            g = db.get(Generation, gid or "")
            f = generation_file(g) if g is not None and g.file_path else None
            if f and f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                out.append((title or mid, f))
            if len(out) >= (len(ids) or n):
                break
    return out


def ollama(model: str, path: Path) -> str:
    root = get_settings().llm_base_url.rstrip("/").removesuffix("/v1")
    body = {"model": model, "stream": False, "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": ASK, "images": [base64.b64encode(path.read_bytes()).decode()]}]}
    try:
        r = httpx.post(f"{root}/api/chat", json=body, timeout=600)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except Exception as e:  # a model that isn't pulled, a timeout: show it in the table, keep going
        return f"(failed: {e})"


def joycaption(comfy: str, path: Path, model: str) -> str:
    # under the data dir, not /tmp: ComfyUI's service may have a private /tmp of its own
    scratch = get_settings().data_dir / "tmp" / "caption-compare"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jc-", dir=scratch) as tmp:
        img = Path(tmp) / f"pic{path.suffix.lower()}"
        shutil.copy(path, img)
        graph = {
            "1": {"class_type": "ImageBatchPath", "inputs": {"image_dir": tmp, "batch_size": 1}},
            "2": {"class_type": "JC", "inputs": {"image": ["1", 0], "model": model, "quantization": "Balanced (8-bit)",
                                                 "prompt_style": "Descriptive", "caption_length": "long",
                                                 "memory_management": "Keep in Memory"}},
            "3": {"class_type": "CaptionSaver", "inputs": {"string": ["2", 0], "image_path": ["1", 1],
                                                           "custom_file_name": "caption"}},
        }
        try:
            r = httpx.post(f"{comfy}/prompt", json={"prompt": graph, "client_id": str(uuid.uuid4())}, timeout=30)
            r.raise_for_status()
            pid = r.json()["prompt_id"]
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                h = httpx.get(f"{comfy}/history/{pid}", timeout=30).json().get(pid)
                if h and h.get("status", {}).get("completed"):
                    break
                if h and h.get("status", {}).get("status_str") == "error":
                    msgs = [m for m in h["status"].get("messages", []) if m[0] == "execution_error"]
                    return f"(failed: {msgs[0][1].get('exception_message') if msgs else 'error'})"
                time.sleep(2)
            txt = Path(tmp) / "caption.txt"
            return txt.read_text(encoding="utf-8").strip() if txt.is_file() else "(no caption written)"
        except Exception as e:
            return f"(failed: {e})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--ids", default="")
    ap.add_argument("--models", default="", help="Ollama vision models to compare (default: LLM_MODEL_VISION,qwen2.5vl:7b)")
    ap.add_argument("--joycaption", default="joycaption-beta-one", help="the node's model name; 'off' to skip")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--html", default="", help="also write a side-by-side page with the pictures")
    a = ap.parse_args()

    models = [m for m in (a.models.split(",") if a.models else [get_settings().llm_model_vision, "qwen2.5vl:7b"]) if m]
    pics = pictures(a.n, [i.strip() for i in a.ids.split(",") if i.strip()])
    if not pics:
        sys.exit("no finished pictures found")
    rows = []
    for k, (title, path) in enumerate(pics, 1):
        print(f"\n=== {k}/{len(pics)}  {title}  ({path.name})")
        caps = {}
        for m in models:
            t0 = time.monotonic()
            caps[m] = ollama(m, path)
            print(f"\n--- {m}  ({time.monotonic() - t0:.0f} s)\n{caps[m]}")
        if a.joycaption != "off":
            t0 = time.monotonic()
            caps["JoyCaption"] = joycaption(a.comfy, path, a.joycaption)
            print(f"\n--- JoyCaption  ({time.monotonic() - t0:.0f} s)\n{caps['JoyCaption']}")
        rows.append((title, path, caps))

    if a.html:
        cols = list(rows[0][2])
        out = ["<!doctype html><meta charset=utf-8><title>Caption compare</title>",
               "<style>body{font:14px system-ui;margin:16px}td{vertical-align:top;padding:8px;border-top:1px solid #ccc;"
               "max-width:380px}img{max-width:260px}</style><table><tr><th>Picture</th>"
               + "".join(f"<th>{html.escape(c)}</th>" for c in cols) + "</tr>"]
        for title, path, caps in rows:
            b64 = base64.b64encode(path.read_bytes()).decode()
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            out.append(f"<tr><td><img src='data:{mime};base64,{b64}'><br>{html.escape(title)}</td>"
                       + "".join(f"<td>{html.escape(caps.get(c, ''))}</td>" for c in cols) + "</tr>")
        Path(a.html).write_text("".join(out) + "</table>", encoding="utf-8")
        print(f"\nwrote {a.html}")


if __name__ == "__main__":
    main()
