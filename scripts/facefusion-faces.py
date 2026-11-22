#!/usr/bin/env python3
"""Find the faces to swap: frames of a video, every face boxed and numbered on one contact sheet.

Run on the GPU box from backend/ (after scripts/install-facefusion.sh):

    uv run python ../scripts/facefusion-faces.py ref.mp4 --at 2,6,10
    uv run python ../scripts/facefusion-faces.py <video media id> --at 2,6,10 --photo <couple photo media id>

Each face's number on the sheet is what facefusion-swap.py's --ref-face takes, with the frame number printed
under it as --ref-frame. With --photo, the photo's faces are numbered too (1 = left), the order the swap crops
them in. Prints the folder with frame_<n>.png, photo faces and sheet.png.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video", help="a video file or a Library media id")
    ap.add_argument("--at", default="2,6,10", help="seconds to look at, comma separated")
    ap.add_argument("--photo", help="the photo with the faces to put in (file or media id)")
    ap.add_argument("--out", help="folder for the frames and the sheet (default faceswap-trials/<time>/)")
    args = ap.parse_args()

    from app import faceswap as fs

    video = fs.resolve(args.video)
    photo = fs.resolve(args.photo) if args.photo else None
    out = Path(args.out) if args.out else ROOT / "faceswap-trials" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    rate = fs.fps(video.path)
    frames = []
    for t in (float(x) for x in args.at.split(",") if x.strip()):
        n = round(t * rate)
        frames.append((t, n, fs.grab(video.path, n, out / f"frame_{n}.png")))
    images = [p for _, _, p in frames] + ([photo.path] if photo else [])
    found = fs.find_faces(images, progress=lambda f, m: print(f"  {m}", flush=True) if m else None)

    shots = []
    print(f"\n{video.title}  ({rate:g} fps)")
    for t, n, p in frames:
        faces = found.get(str(p)) or []
        shots.append((f"{t:g} s · frame {n}", p, faces))
        print(f"  {t:g} s = --ref-frame {n}: {len(faces)} face(s)  "
              + "  ".join(f"{i}={f['gender']}" for i, f in enumerate(faces, start=1)))
    if photo:
        faces = found.get(str(photo.path)) or []
        shots.append(("photo", photo.path, faces))
        crops = fs.crop_faces(photo.path, out / "photo_faces", faces)
        print(f"  photo: {len(faces)} face(s), cropped to {out / 'photo_faces'}  (1 = left: "
              f"{', '.join(c.name for c in crops)})")
    sheet = fs.contact_sheet(shots, out / "sheet.png")
    print(f"\nSheet: {sheet}\nFolder: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
