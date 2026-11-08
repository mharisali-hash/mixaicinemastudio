#!/usr/bin/env python3
"""Try ACE-Step 1.5 restyle and repaint on one song, with stock ComfyUI nodes only (M24 phase 3 trial).

check-ace15-remix.py showed (8 Nov) that the server has no cover node, but has everything for a latent trick:
encode the song with the ACE-Step VAE, then let the sampler redo part of it.
  restyle  the whole clip re-sampled at a partial denoise with new style words; low denoise keeps more of the song
  repaint  only a 15 s stretch regenerated (SetLatentNoiseMask); the rest should stay as it was
Nothing in the app changes. Each trial is one short GPU run on a 60 s clip.

Run on the GPU box, from the repo, with the backend's Python:

    backend/.venv/bin/python scripts/try-ace15-remix.py "harbour"          # a Library song whose title has "harbour"
    backend/.venv/bin/python scripts/try-ace15-remix.py /path/to/song.mp3 --tags "deep house, warm bass"
    backend/.venv/bin/python scripts/try-ace15-remix.py "harbour" --dry-run  # print the graphs, run nothing

A Library song's trials land in the same Library, titled "Trial — …", so they can be played in the browser.
Every file is also written to remix-trials/<time>/ next to this script's repo.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
CKPT = "ace_step_1.5_turbo_aio.safetensors"
CLIP_S = 60.0
START_S = 30.0
REPAINT = (20.0, 35.0)
MASK_PX_PER_S = 10
DEFAULT_TAGS = "deep house, warm bass, four on the floor, airy pads"


# ---------------------------------------------------------------- graphs

def _base(song: str, tags: str, bpm: int, key: str, seed: int, seconds: float) -> dict:
    return {
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "msamp": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["ckpt", 0], "shift": 3.0}},
        "song": {"class_type": "LoadAudio", "inputs": {"audio": song}},
        "enc": {"class_type": "VAEEncodeAudio", "inputs": {"audio": ["song", 0], "vae": ["ckpt", 2]}},
        # no lyrics and no planning LM: the song's own latent carries the structure
        "text": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
            "clip": ["ckpt", 1], "tags": tags, "lyrics": "", "seed": seed, "bpm": bpm, "duration": seconds,
            "timesignature": "4", "language": "en", "keyscale": key, "generate_audio_codes": False,
            "cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "dec": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sampler", 0], "vae": ["ckpt", 2]}},
    }


def _sampler(latent, positive, seed: int, denoise: float, steps: int = 8) -> dict:
    return {"class_type": "KSampler", "inputs": {
        "model": ["msamp", 0], "positive": positive, "negative": ["neg", 0], "latent_image": latent,
        "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
        "denoise": denoise}}


def restyle_graph(song, tags, bpm, key, seed, seconds, denoise, timbre=False, prefix="mixai/remix_trial") -> dict:
    g = _base(song, tags, bpm, key, seed, seconds)
    positive = ["text", 0]
    if timbre:
        # the song as its own timbre reference: does the singer's sound survive a stronger restyle?
        g["timbre"] = {"class_type": "ReferenceTimbreAudio", "inputs": {"conditioning": ["text", 0],
                                                                         "latent": ["enc", 0]}}
        positive = ["timbre", 0]
    g["sampler"] = _sampler(["enc", 0], positive, seed, denoise)
    g["save"] = {"class_type": "SaveAudio", "inputs": {"audio": ["dec", 0], "filename_prefix": prefix}}
    return g


def repaint_graph(song, tags, bpm, key, seed, seconds, span, denoise=1.0, prefix="mixai/remix_trial") -> dict:
    """A mask the length of the clip (10 px a second), white over the span to redo."""
    g = _base(song, tags, bpm, key, seed, seconds)
    width = int(round(seconds * MASK_PX_PER_S))
    a, b = (int(round(t * MASK_PX_PER_S)) for t in span)
    g["mask0"] = {"class_type": "SolidMask", "inputs": {"value": 0.0, "width": width, "height": 1}}
    g["mask1"] = {"class_type": "SolidMask", "inputs": {"value": 1.0, "width": max(1, b - a), "height": 1}}
    g["mask"] = {"class_type": "MaskComposite", "inputs": {"destination": ["mask0", 0], "source": ["mask1", 0],
                                                           "x": a, "y": 0, "operation": "add"}}
    g["masked"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["enc", 0], "mask": ["mask", 0]}}
    g["sampler"] = _sampler(["masked", 0], ["text", 0], seed, denoise)
    g["save"] = {"class_type": "SaveAudio", "inputs": {"audio": ["dec", 0], "filename_prefix": prefix}}
    return g


def trials(song, tags, bpm, key, seed, seconds) -> list[tuple[str, dict]]:
    out = [(f"restyle {d:.2f}", restyle_graph(song, tags, bpm, key, seed, seconds, d)) for d in (0.35, 0.5, 0.65)]
    out.append(("restyle 0.65 + own timbre", restyle_graph(song, tags, bpm, key, seed, seconds, 0.65, timbre=True)))
    out.append((f"repaint {REPAINT[0]:g}-{REPAINT[1]:g} s", repaint_graph(song, tags, bpm, key, seed, seconds, REPAINT)))
    return out


# ---------------------------------------------------------------- ComfyUI

class Comfy:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.client = uuid.uuid4().hex

    def _req(self, method, path, body=None, headers=None, timeout=60):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def upload(self, path: Path) -> str:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("type", "input"), ("subfolder", "mixai"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
                     f"Content-Type: audio/flac\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = json.loads(self._req("POST", "/upload/image", b"".join(parts),
                                    {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=300))
        return f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]

    def run(self, graph: dict, timeout: float = 900) -> list[dict]:
        try:
            r = json.loads(self._req("POST", "/prompt", json.dumps({"prompt": graph, "client_id": self.client})
                                     .encode(), {"Content-Type": "application/json"}))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ComfyUI refused the graph: {e.read().decode(errors='replace')[:800]}") from None
        pid = r["prompt_id"]
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(2)
            hist = json.loads(self._req("GET", f"/history/{pid}")).get(pid)
            if not hist:
                continue
            status = hist.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m[1] for m in status.get("messages") or [] if m and m[0] == "execution_error"]
                why = (msgs[0].get("exception_message") if msgs else None) or "unknown error"
                raise RuntimeError(f"ComfyUI failed: {why.strip()[:800]}")
            files = [a for o in (hist.get("outputs") or {}).values() for a in (o.get("audio") or [])]
            if files or status.get("completed"):
                return files
        raise RuntimeError("timed out")

    def download(self, f: dict, dst: Path) -> Path:
        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""),
                                    "type": f.get("type", "output")})
        dst.write_bytes(self._req("GET", f"/view?{q}", timeout=300))
        return dst


# ---------------------------------------------------------------- the app side (optional)

def ffmpeg() -> str:
    try:
        _app()
        from app.config import get_settings

        return get_settings().ffmpeg_path()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def _app():
    sys.path.insert(0, str(BACKEND))
    import os

    os.chdir(BACKEND)  # the settings read backend/.env
    from app import library as lib
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Generation, MediaItem, new_id
    from app.services import generation_file, new_seed

    return lib, get_settings, SessionLocal, Generation, MediaItem, new_id, generation_file, new_seed


def find_song(query: str):
    lib, _, SessionLocal, Generation, MediaItem, _, generation_file, _ = _app()
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = db.scalars(select(MediaItem).where(MediaItem.kind == "audio", MediaItem.title.ilike(f"%{query}%"))
                          .order_by(MediaItem.created_at.desc()).limit(10)).all() if query.strip() else []
        for item in rows:
            g = db.get(Generation, item.generation_id) if item.generation_id else None
            path = generation_file(g) if g is not None else None
            if path is not None and path.is_file():
                return path, {"workspace_id": item.workspace_id, "title": item.title, "folder_id": item.folder_id}
        recent = db.scalars(select(MediaItem).where(MediaItem.kind == "audio", MediaItem.generation_id.is_not(None),
                                                    MediaItem.title != "")
                            .order_by(MediaItem.created_at.desc()).limit(15)).all()
        names = "\n".join(f"  {i.title}" for i in recent) or "  (none)"
    head = f"No finished Library song has “{query}” in its title." if query.strip() else "Which song?"
    raise SystemExit(f"{head} The latest audio in the Library:\n{names}\n"
                     "Run again with a few words from one of those titles, in quotes.")


def to_library(src: dict, path: Path, label: str, tags: str) -> str:
    lib, _, SessionLocal, Generation, MediaItem, new_id, _, new_seed = _app()
    with SessionLocal() as db:
        folder, rel = lib.media_dir(src["workspace_id"])
        folder.mkdir(parents=True, exist_ok=True)
        gid = new_id()
        dst = folder / f"{gid}.flac"
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(path), "-c:a", "flac", str(dst)], check=True)
        title = f"Trial — {label} — {src['title'] or 'Untitled'}"[:240]
        item = MediaItem(id=new_id(), workspace_id=src["workspace_id"], kind="audio", origin="generated",
                         title=title, tags=["remix_trial"], duration_s=CLIP_S, folder_id=src["folder_id"])
        db.add(item)
        db.flush()
        g = Generation(id=gid, workspace_id=src["workspace_id"], project_id=None, target_type="media",
                       target_id=item.id, kind="audio", version=1, status="ready", prompt=tags, seed=new_seed(),
                       media_type="audio/flac", file_path=f"{rel}/{dst.name}",
                       params={"kind": "remix_trial", "trial": label, "tags": tags})
        db.add(g)
        db.flush()
        item.generation_id = g.id
        lib.touch(item)
        db.commit()
        return title


# ---------------------------------------------------------------- main

def analyse(path: Path):
    try:
        _app()
        from app import remix_analysis as ra

        got = ra.analyse(path)
        return got.get("bpm"), got.get("key")
    except Exception as e:  # noqa: BLE001 - the trial still runs with guesses
        print(f"(couldn't hear the tempo and key: {e})")
        return None, None


def comfy_url() -> str:
    try:
        _app()
        from app.config import get_settings

        urls = get_settings().comfy_urls_for("image")
        if urls:
            return urls[0]
    except Exception:  # noqa: BLE001
        pass
    return "http://127.0.0.1:8188"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("song", nargs="?", default="",
                    help="a song file, or words from a Library song's title (leave out to list the latest)")
    ap.add_argument("--tags", default=DEFAULT_TAGS, help="style words for the new version")
    ap.add_argument("--start", type=float, default=START_S, help="where the 60 s clip starts (s)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--url", help="ComfyUI address (default: the app's audio ComfyUI)")
    ap.add_argument("--bpm", type=int)
    ap.add_argument("--key", help='e.g. "A minor"')
    ap.add_argument("--dry-run", action="store_true", help="print the graphs only")
    args = ap.parse_args()

    src = None
    # resolved now: reading the app's settings moves into backend/
    path = Path(args.song).resolve() if args.song else Path("/nonexistent")
    if not path.is_file():
        path, src = find_song(args.song)
        print(f"Library song: {src['title']}  ({path})")

    out = ROOT / "remix-trials" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    clip = out / "original_clip.flac"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", str(args.start), "-t", str(CLIP_S), "-i", str(path),
                    "-vn", "-ac", "2", "-ar", "48000", "-c:a", "flac", str(clip)], check=True)
    bpm, key = args.bpm, args.key
    if bpm is None or key is None:
        heard_bpm, heard_key = analyse(clip)
        bpm = bpm or int(round(heard_bpm or 120))
        key = key or heard_key or "C major"
    print(f"Clip: {CLIP_S:g} s from {args.start:g} s · {bpm} BPM · {key} · tags: {args.tags}")

    comfy = Comfy(args.url or comfy_url())
    song = "mixai/remix_trial_source.flac" if args.dry_run else comfy.upload(clip)
    plans = trials(song, args.tags, bpm, key, args.seed, CLIP_S)
    if args.dry_run:
        for label, g in plans:
            print(f"\n== {label}\n{json.dumps(g, indent=1)}")
        return 0

    print(f"ComfyUI: {comfy.base}\n")
    results = []
    for n, (label, graph) in enumerate(plans, start=1):
        t0 = time.monotonic()
        try:
            files = comfy.run(graph)
            if not files:
                raise RuntimeError("no audio came back")
            dst = comfy.download(files[0], out / f"{n}_{label.replace(' ', '_').replace('+', 'and')}"
                                 f"{Path(files[0]['filename']).suffix}")
            line = f"ok    {label:<28} {time.monotonic() - t0:5.1f} s  {dst.name}"
            if src is not None:
                line += f"\n      in the Library as “{to_library(src, dst, label, args.tags)}”"
            results.append(line)
        except Exception as e:  # noqa: BLE001 - one failed trial says something too
            results.append(f"FAIL  {label:<28} {e}")
        print(results[-1])
    print(f"\nFiles: {out}")
    print("Listen to each next to original_clip.flac and paste this output back with what you hear:")
    print("does the restyle keep the melody and vocals, which strength sounds best, and does the repaint")
    print(f"change only {REPAINT[0]:g}-{REPAINT[1]:g} s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
