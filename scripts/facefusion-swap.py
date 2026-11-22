#!/usr/bin/env python3
"""Swap faces from a photo into a video with FaceFusion (face swap stage 1, a private test).

Run on the GPU box from backend/, after scripts/install-facefusion.sh and facefusion-faces.py (whose sheet gives
the frame and face numbers):

    # the couple's photo from the Library: its left face goes on the first person, its right on the second
    uv run python ../scripts/facefusion-swap.py --video <video file or media id> --photo <photo media id> \\
        --ref-frame 60 --ref-face 1 --ref-frame 120 --ref-face 2

    # or face pictures you already have, one per person
    uv run python ../scripts/facefusion-swap.py --video ref.mp4 --face groom.jpg --ref-frame 60 --ref-face 1 \\
        --face bride.jpg --ref-frame 120 --ref-face 2 --out wedding-swapped.mp4

Without --out the result goes into the Library (the video's or the photo's workspace, or --workspace) as a
private video titled "… · face-swapped · test", through the usual finished-video check, and its media id is
printed. One pass per person, in the order given; only the person picked on the reference frame changes.
"""
import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def pick(crops: list[Path], which: str) -> Path:
    if not crops:
        raise SystemExit("FaceFusion found no face on the photo")
    if which == "left":
        return crops[0]
    if which == "right":
        return crops[-1]
    n = int(which)
    if not 1 <= n <= len(crops):
        raise SystemExit(f"the photo has {len(crops)} face(s); there is no face {n}")
    return crops[n - 1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--video", required=True, help="the video to put the faces into (file or Library media id)")
    ap.add_argument("--face", action="append", default=[], help="a picture of one person's face; once per person")
    ap.add_argument("--photo", help="a photo with everyone's faces (file or media id), instead of --face")
    ap.add_argument("--take", default="left,right",
                    help="with --photo: which of its faces, in pass order (left, right or a number; default left,right)")
    ap.add_argument("--ref-frame", type=int, action="append", default=[], help="frame number, once per person")
    ap.add_argument("--ref-face", type=int, action="append", default=[],
                    help="that person's number on the frame (1 = left, as on the contact sheet), once per person")
    ap.add_argument("--model", default="inswapper_128_fp16", choices=["inswapper_128_fp16", "hyperswap_1a_256"])
    ap.add_argument("--pixel-boost", default="512x512", help="FaceFusion's face resolution (128x128 ... 1024x1024)")
    ap.add_argument("--workspace", help="Library workspace for the result (default: the video's or the photo's)")
    ap.add_argument("--title", help="title in the Library (default: the video's, marked face-swapped)")
    ap.add_argument("--out", help="just write the result to this file, no Library item")
    args = ap.parse_args(argv)

    from app import faceswap as fs

    if bool(args.face) == bool(args.photo):
        ap.error("give either --face (once per person) or --photo")
    if len(args.ref_frame) != len(args.ref_face):
        ap.error("every --ref-frame needs its --ref-face")

    video = fs.resolve(args.video)
    tmp = Path(tempfile.mkdtemp(prefix="faceswap-"))
    try:
        photo = None
        if args.photo:
            photo = fs.resolve(args.photo)
            crops = fs.crop_faces(photo.path, tmp / "photo_faces")
            faces = [pick(crops, w.strip()) for w in args.take.split(",") if w.strip()]
        else:
            faces = [fs.resolve(f).path for f in args.face]
        refs = list(zip(args.ref_frame, args.ref_face))
        if len(refs) != len(faces):
            ap.error(f"{len(faces)} face(s) but {len(refs)} --ref-frame/--ref-face pair(s)")

        workspace = args.workspace or video.workspace_id or (photo.workspace_id if photo else None)
        if not args.out and not workspace:
            ap.error("the video and photo are plain files: give --workspace for the Library, or --out")

        out = tmp / "swapped.mp4"
        t0 = time.monotonic()
        done = fs.swap(video.path, faces, refs, out, model=args.model, pixel_boost=args.pixel_boost,
                       progress=lambda f, m: print(f"  {f:4.0%} {m}", flush=True))
        took = time.monotonic() - t0
        print(f"Swapped in {took:.0f} s (passes: {', '.join(f'{s:g} s' for s in done.get('seconds') or [])})")

        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out, args.out)
            print(f"Written: {Path(args.out).resolve()}")
            return 0
        title = args.title or f"{video.title} · face-swapped · test"
        params = {"model": args.model, "pixel_boost": args.pixel_boost, "seconds": round(took, 1),
                  "source_video": video.item_id or video.path.name,
                  "source_photo": (photo.item_id or photo.path.name) if photo else None,
                  "refs": [{"frame": f, "face": n} for f, n in refs]}
        try:
            folder = video.folder_id if workspace == video.workspace_id else None
            item_id = fs.to_library(out, workspace, title, params, folder_id=folder)
        except RuntimeError as e:
            raise SystemExit(f"The finished video was refused: {e}") from None
        print(f"In the Library as “{title}”\nmedia id: {item_id}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
