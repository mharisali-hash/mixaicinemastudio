"""Face swap, stage 1 (owner, 22 Nov): FaceFusion swaps a photo's faces into an existing video.

No route or UI yet; scripts/facefusion-faces.py and scripts/facefusion-swap.py drive this on the server.
FaceFusion runs in its own venv (scripts/install-facefusion.sh) through venv_runner, on the video card. Its
swapper and detector weights are non-commercial, so everything here sits behind FACESWAP_ALLOW_NONCOMMERCIAL.
"""
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from app import venv_runner
from app.config import get_settings

OFF = "Face swap is off (FACESWAP_ALLOW_NONCOMMERCIAL=false): the models it needs are non-commercial"
# a face crop needs its hair and jaw around it, or FaceFusion's detector can't find the face again
CROP_MARGIN = 0.6


@dataclass
class Source:
    path: Path
    title: str
    workspace_id: str | None = None
    folder_id: str | None = None
    item_id: str | None = None


def resolve(ref: str) -> Source:
    """A file on disk, or a Library media id (its current version's file)."""
    p = Path(ref)
    if p.is_file():
        return Source(p.resolve(), p.stem)
    from app.db import SessionLocal
    from app.models import Generation, MediaItem
    from app.services import generation_file

    with SessionLocal() as db:
        item = db.get(MediaItem, ref)
        if item is None:
            raise ValueError(f"{ref} is neither a file nor a Library media id")
        g = db.get(Generation, item.generation_id) if item.generation_id else None
        path = generation_file(g) if g is not None else None
        if path is None or not path.is_file():
            raise ValueError(f"the Library item {ref} has no file on this machine")
        return Source(path, item.title or "Untitled", item.workspace_id, item.folder_id, item.id)


def _ffmpeg() -> str:
    return get_settings().ffmpeg_path()


def fps(video: Path) -> float:
    from app.uploads import probe_video

    return probe_video(video).fps or 25.0


def grab(video: Path, frame: int, dest: Path) -> Path:
    # by frame number, the way FaceFusion's --reference-frame-number counts, not by time
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-vf", f"select=eq(n\\,{frame})",
                    "-fps_mode", "passthrough", "-frames:v", "1", str(dest)], check=True)
    if not dest.is_file():
        raise RuntimeError(f"no frame {frame} in {video.name}")
    return dest


def find_faces(images: list[Path], progress: Callable[[float, str], None] | None = None) -> dict[str, list[dict]]:
    s = get_settings()
    done = venv_runner.run("facefusion_faces", s.facefusion_venv,
                           {"repo": s.facefusion_repo, "images": [str(p) for p in images]},
                           lane="video", need_gb=2, progress=progress)
    return done.get("faces") or {}


def crop_faces(photo: Path, out_dir: Path, faces: list[dict] | None = None) -> list[Path]:
    """Every face on the photo as its own picture, left to right."""
    from PIL import Image, ImageOps

    if faces is None:
        faces = find_faces([photo]).get(str(photo)) or []
    out_dir.mkdir(parents=True, exist_ok=True)
    crops = []
    with Image.open(photo) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        for n, f in enumerate(faces, start=1):
            x1, y1, x2, y2 = f["box"]
            mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
            box = (max(0, int(x1 - mx)), max(0, int(y1 - my)), min(im.width, int(x2 + mx)), min(im.height, int(y2 + my)))
            dest = out_dir / f"face{n}.png"
            im.crop(box).save(dest)
            crops.append(dest)
    return crops


def contact_sheet(shots: list[tuple[str, Path, list[dict]]], dest: Path, height: int = 640) -> Path:
    """The frames side by side, every face boxed and numbered 1, 2, 3 ... left to right (as --ref-face takes them)."""
    from PIL import Image, ImageDraw

    tiles = []
    for label, path, faces in shots:
        with Image.open(path) as im:
            scale = height / im.height
            tile = im.convert("RGB").resize((max(1, round(im.width * scale)), height))
        d = ImageDraw.Draw(tile)
        font = _font(max(18, height // 22))
        for n, f in enumerate(faces, start=1):
            x1, y1, x2, y2 = (v * scale for v in f["box"])
            d.rectangle((x1, y1, x2, y2), outline=(255, 214, 0), width=3)
            d.text((x1 + 4, max(0, y1 - font.size - 6)), str(n), fill=(255, 214, 0), font=font,
                   stroke_width=2, stroke_fill=(0, 0, 0))
        d.text((8, height - font.size - 10), label, fill=(255, 255, 255), font=font, stroke_width=2,
               stroke_fill=(0, 0, 0))
        tiles.append(tile)
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 8 * (len(tiles) - 1), height), (20, 20, 20))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + 8
    sheet.save(dest)
    return dest


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def swap(video: Path, faces: list[Path], refs: list[tuple[int, int]], out: Path, *, model: str = "inswapper_128_fp16",
         pixel_boost: str = "512x512", progress: Callable[[float, str], None] | None = None) -> dict:
    """One pass per face, in order. refs are (frame number, face number on that frame counted from 1)."""
    s = get_settings()
    if not s.faceswap_allow_noncommercial:
        raise RuntimeError(OFF)
    if not faces or len(faces) != len(refs):
        raise ValueError("give one reference frame and face number for every face")
    passes = [{"source": str(f), "ref_frame": int(frame), "ref_face": int(n) - 1} for f, (frame, n) in zip(faces, refs)]
    if any(p["ref_face"] < 0 for p in passes):
        raise ValueError("face numbers start at 1, as on the contact sheet")
    job = {"repo": s.facefusion_repo, "target": str(video), "out": str(out), "passes": passes, "model": model,
           "pixel_boost": pixel_boost, "quality": 90}
    # a 13 s clip is minutes, but a long one at 512 px boost can pass the usual runner limit
    return venv_runner.run("facefusion_swap", s.facefusion_venv, job, lane="video", need_gb=s.faceswap_vram_gb,
                           progress=progress, timeout=max(s.audio_runner_timeout_s, 3600))


def to_library(path: Path, workspace_id: str, title: str, params: dict, folder_id: str | None = None) -> str:
    """The result as a private Library video. It goes through the same finished-picture check as any video;
    a refusal deletes it again and raises RuntimeError with the reason."""
    from app import content_policy
    from app import library as lib
    from app.db import SessionLocal
    from app.models import Generation, MediaItem, Workspace, new_id
    from app.services import new_seed
    from app.uploads import probe_video

    info = probe_video(path)
    with SessionLocal() as db:
        ws = db.get(Workspace, workspace_id)
        if ws is None:
            raise ValueError(f"no workspace {workspace_id}")
        folder, rel = lib.media_dir(workspace_id)
        folder.mkdir(parents=True, exist_ok=True)
        gid = new_id()
        dst = folder / f"{gid}{path.suffix or '.mp4'}"
        shutil.copyfile(path, dst)
        item = MediaItem(id=new_id(), workspace_id=workspace_id, kind="video", origin="generated", title=title[:300],
                         tags=["face-swapped", "test"], width=info.width, height=info.height,
                         duration_s=info.duration_s, folder_id=folder_id)
        db.add(item)
        db.flush()
        # kind "video" so content_policy treats it as one (its picture check only looks at visual kinds)
        g = Generation(id=gid, workspace_id=workspace_id, project_id=None, target_type="media", target_id=item.id,
                       kind="video", version=1, status="ready", prompt="face swap test", seed=new_seed(),
                       media_type="video/mp4", file_path=f"{rel}/{dst.name}", params={"kind": "face_swap", **params})
        db.add(g)
        db.flush()
        item.generation_id = g.id
        lib.touch(item)
        ctx = SimpleNamespace(db=db, job=SimpleNamespace(user_id=ws.owner_user_id), progress=lambda *a, **k: None)
        try:
            content_policy.check_after(ctx, g, dst, "video/mp4")
        except RuntimeError:
            # the check already deleted the file and recorded why; the rows go too
            db.delete(g)
            db.delete(item)
            db.commit()
            raise
        db.commit()
        return item.id
