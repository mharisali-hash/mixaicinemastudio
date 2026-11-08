#!/usr/bin/env python3
"""Does this ComfyUI have what an ACE-Step 1.5 cover or repaint needs? (M24 phase 3)

Reads /object_info only; nothing is queued and no GPU is used. Run on the GPU box:

    python3 scripts/check-ace15-remix.py                  # the URL from backend/.env, else :8188
    python3 scripts/check-ace15-remix.py http://127.0.0.1:8189

Paste the whole output back.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

WORDS = ("acestep",)
# inputs that would let a song be re-made from its own audio, or one stretch of it
HINTS = ("src_audio", "source_audio", "reference_audio", "audio", "latent", "repaint", "noise_mask", "mask",
         "start", "end", "cover", "strength", "denoise", "retake", "variance", "edit", "audio_codes")
# custom node packs that add cover / repaint / extend for ACE-Step 1.5 (ComfyUI_RyanOnTheInside)
PACKS = {"ACEStep15NativeCoverGuider": "cover", "ACEStep15NativeEditGuider": "repaint / extend"}
# core nodes that make "encode the song, denoise part of it" possible even without a dedicated node
CORE = ("VAEEncodeAudio", "VAEDecodeAudio", "SetLatentNoiseMask", "KSamplerAdvanced", "SamplerCustomAdvanced",
        "LoadAudio", "EmptyAceStep1.5LatentAudio", "EmptyAceStepLatentAudio")


def url_from_env() -> str:
    env = Path(__file__).resolve().parents[1] / "backend" / ".env"
    vals = {}
    if env.is_file():
        for line in env.read_text(errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    for key in ("COMFY_AUDIO_URLS", "COMFY_IMAGE_URLS", "COMFY_URLS", "COMFY_URL"):
        v = os.environ.get(key) or vals.get(key)
        if v:
            return v.split(",")[0].strip()
    return "http://127.0.0.1:8188"


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else url_from_env()).rstrip("/")
    print(f"ComfyUI: {base}")
    try:
        with urllib.request.urlopen(f"{base}/object_info", timeout=60) as r:
            info = json.load(r)
    except Exception as e:  # noqa: BLE001
        print(f"Can't read /object_info: {e}")
        return 1
    print(f"{len(info)} node types\n")

    # "ace" alone matched replace / face / space (8 Nov); the node names spell it AceStep or ACEStep
    ace = {k: v for k, v in info.items() if any(w in k.lower().replace("_", "") for w in WORDS)}
    print(f"ACE-Step nodes ({len(ace)}):")
    found = set()
    for name, node in sorted(ace.items()):
        spec = node.get("input") or {}
        inputs = {**(spec.get("required") or {}), **(spec.get("optional") or {})}
        print(f"  {name}  [{node.get('python_module', '?')}]")
        for iname, ispec in inputs.items():
            kind = ispec[0] if isinstance(ispec, (list, tuple)) and ispec else ispec
            kind = kind if isinstance(kind, str) else "choice"
            mark = " <-" if any(h in iname.lower() for h in HINTS) else ""
            if mark:
                found.add(iname)
            print(f"      {iname}: {kind}{mark}")
    print()
    print("Known cover / repaint nodes:")
    for name, what in PACKS.items():
        print(f"  {'yes' if name in info else 'no '}  {name}  ({what})")
    others = sorted(k for k in info if any(w in k.lower() for w in ("cover", "repaint", "retake", "extend"))
                    and k not in PACKS)
    if others:
        print("  other nodes with cover/repaint/retake/extend in the name: " + ", ".join(others))
    print()
    print("Core nodes for a latent repaint:")
    for name in CORE:
        print(f"  {'yes' if name in info else 'no '}  {name}")
    print()
    cover = "ACEStep15NativeCoverGuider" in info or any(
        k in found for k in ("src_audio", "source_audio", "source_latents"))
    repaint = ("SetLatentNoiseMask" in info and "VAEEncodeAudio" in info) or any("repaint" in k for k in found)
    print(f"Cover (source-audio conditioning) looks possible: {'yes' if cover else 'not seen'}")
    print(f"Repaint (encode, mask a range, denoise) looks possible: {'yes' if repaint else 'not seen'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
