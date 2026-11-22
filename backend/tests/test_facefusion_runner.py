"""Face swap stage 1 (22 Nov): the FaceFusion runner and scripts/facefusion-swap.py, against a fake FaceFusion."""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import content_policy, faceswap, venv_runner
from app.config import get_settings
from app.db import SessionLocal
from app.models import Generation, MediaItem, Workspace

REPO = Path(__file__).resolve().parents[2]
RUNNERS = REPO / "backend" / "runners"

# stands in for facefusion.py: notes its arguments and "swaps" by copying the target to the output
FAKE = """import json, shutil, sys
a = sys.argv[1:]
open(__file__ + ".calls", "a").write(json.dumps(a) + "\\n")
shutil.copyfile(a[a.index("--target-path") + 1], a[a.index("--output-path") + 1])
"""


def _video(dest: Path) -> Path:
    subprocess.run([get_settings().ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=64x96:rate=25:duration=1", "-pix_fmt", "yuv420p", str(dest)], check=True)
    return dest


def _calls(repo: Path) -> list[list[str]]:
    return [json.loads(x) for x in (repo / "facefusion.py.calls").read_text().splitlines()]


def _arg(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


@pytest.fixture
def fake_ff(tmp_path, monkeypatch):
    repo = tmp_path / "facefusion"
    repo.mkdir()
    (repo / "facefusion.py").write_text(FAKE)
    monkeypatch.setattr(get_settings(), "facefusion_repo", str(repo))
    # the runner runs in this Python instead of the server's facefusion venv
    monkeypatch.setattr(venv_runner, "python_for", lambda venv: Path(sys.executable))
    return repo


@pytest.fixture
def checks(monkeypatch):
    seen = []
    monkeypatch.setattr(content_policy, "check_after", lambda ctx, gen, path, mt: seen.append((gen.kind, path, mt)))
    return seen


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(RUNNERS))
    # runner_io swaps sys.stdout for stderr when it's imported; put it back after the test
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    spec = importlib.util.spec_from_file_location("facefusion_swap", RUNNERS / "facefusion_swap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _script():
    spec = importlib.util.spec_from_file_location("facefusion_swap_script", REPO / "scripts" / "facefusion-swap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _workspace() -> str:
    with SessionLocal() as db:
        return db.query(Workspace).first().id


def test_two_passes_in_reference_mode(fake_ff, tmp_path):
    video = _video(tmp_path / "ref.mp4")
    groom, bride = tmp_path / "groom.png", tmp_path / "bride.png"
    out = tmp_path / "out.mp4"
    done = faceswap.swap(video, [groom, bride], [(60, 1), (120, 2)], out)
    assert out.is_file() and out.read_bytes() == video.read_bytes()
    assert len(done["seconds"]) == 2

    first, second = _calls(fake_ff)
    for cmd, face, frame, pos in ((first, groom, "60", "0"), (second, bride, "120", "1")):
        assert cmd[0] == "headless-run"
        assert _arg(cmd, "--source-paths") == str(face)
        assert _arg(cmd, "--face-selector-mode") == "reference"
        assert _arg(cmd, "--face-selector-order") == "left-right"
        # the sheet counts from 1, FaceFusion from 0
        assert _arg(cmd, "--reference-frame-number") == frame and _arg(cmd, "--reference-face-position") == pos
        assert cmd[cmd.index("--processors") + 1:cmd.index("--processors") + 4] == [
            "face_swapper", "expression_restorer", "face_enhancer"]
        assert _arg(cmd, "--face-swapper-model") == "inswapper_128_fp16"
        assert _arg(cmd, "--face-enhancer-model") == "gfpgan_1.4"
        assert _arg(cmd, "--expression-restorer-model") == "live_portrait"
        assert _arg(cmd, "--execution-providers") == "cuda" and _arg(cmd, "--output-video-quality") == "90"
    # the bride is swapped into the groom's result, not the original
    assert _arg(first, "--target-path") == str(video)
    assert _arg(second, "--target-path") == _arg(first, "--output-path")


def test_hyperswap_and_bad_face_numbers(fake_ff, tmp_path):
    video = _video(tmp_path / "ref.mp4")
    faceswap.swap(video, [tmp_path / "a.png"], [(0, 1)], tmp_path / "o.mp4", model="hyperswap_1a_256")
    assert _arg(_calls(fake_ff)[0], "--face-swapper-model") == "hyperswap_1a_256"
    with pytest.raises(ValueError):
        faceswap.swap(video, [tmp_path / "a.png"], [(0, 0)], tmp_path / "o.mp4")
    with pytest.raises(ValueError):
        faceswap.swap(video, [tmp_path / "a.png"], [], tmp_path / "o.mp4")


def test_off_without_the_test_mode_switch(fake_ff, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "faceswap_allow_noncommercial", False)
    with pytest.raises(RuntimeError, match="FACESWAP_ALLOW_NONCOMMERCIAL"):
        faceswap.swap(tmp_path / "v.mp4", [tmp_path / "a.png"], [(0, 1)], tmp_path / "o.mp4")
    assert not (fake_ff / "facefusion.py.calls").exists()


def test_progress_ticks_while_facefusion_works(runner, tmp_path, monkeypatch):
    ticks = []
    monkeypatch.setattr(runner.rio, "progress", lambda f, m="": ticks.append((f, m)))
    monkeypatch.setattr(runner, "TICK_S", 0.02)

    def slow(cmd, **kw):
        time.sleep(0.2)
        Path(_arg(cmd, "--output-path")).write_bytes(b"x" * 2000)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(runner.subprocess, "run", slow)
    (tmp_path / "facefusion.py").write_text("")
    out = tmp_path / "out.mp4"
    runner.run({"repo": str(tmp_path), "target": "t.mp4", "out": str(out),
                "passes": [{"source": "a.png", "ref_frame": 0, "ref_face": 0},
                           {"source": "b.png", "ref_frame": 5, "ref_face": 1}]})
    assert out.is_file()
    swapping = [m for _, m in ticks if m.startswith("Swapping face")]
    # the heartbeat ticks during each pass, not only at its start
    assert swapping.count("Swapping face 1 of 2") > 2 and swapping.count("Swapping face 2 of 2") > 2
    assert ticks[-1] == (1.0, "Swapped")


def test_empty_output_is_a_failure(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner.rio, "progress", lambda *a: None)
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    (tmp_path / "facefusion.py").write_text("")
    with pytest.raises(RuntimeError, match="made nothing on pass 1"):
        runner.run({"repo": str(tmp_path), "target": "t.mp4", "out": str(tmp_path / "o.mp4"),
                    "passes": [{"source": "a.png", "ref_frame": 0, "ref_face": 0}]})


def test_the_script_puts_it_in_the_library(client, fake_ff, checks, tmp_path, capsys):
    ws = _workspace()
    src_id = faceswap.to_library(_video(tmp_path / "ref.mp4"), ws, "Wedding dance", {})
    checks.clear()
    groom, bride = tmp_path / "groom.png", tmp_path / "bride.png"
    groom.write_bytes(b"g")
    bride.write_bytes(b"b")

    assert _script().main(["--video", src_id, "--face", str(groom), "--ref-frame", "10", "--ref-face", "1",
                           "--face", str(bride), "--ref-frame", "20", "--ref-face", "2"]) == 0
    assert len(_calls(fake_ff)) == 2
    new_id = capsys.readouterr().out.strip().splitlines()[-1].split()[-1]
    with SessionLocal() as db:
        item = db.get(MediaItem, new_id)
        g = db.get(Generation, item.generation_id)
        assert item.workspace_id == ws and item.kind == "video"
        assert item.title == "Wedding dance · face-swapped · test" and "face-swapped" in item.tags
        assert g.kind == "video" and g.target_type == "media" and g.params["kind"] == "face_swap"
        assert g.params["refs"] == [{"frame": 10, "face": 1}, {"frame": 20, "face": 2}]
        assert (get_settings().data_dir / g.file_path).is_file()
    # the finished-video check ran on it, as a video
    assert [(k, mt) for k, _, mt in checks] == [("video", "video/mp4")]
    assert checks[0][1] == get_settings().data_dir / g.file_path


def test_the_photo_is_cropped_left_then_right(client, fake_ff, checks, tmp_path, monkeypatch, capsys):
    from PIL import Image

    photo = tmp_path / "couple.png"
    Image.new("RGB", (400, 200), (90, 90, 90)).save(photo)
    # FaceFusion's finder, as the runner would answer: right face listed second
    monkeypatch.setattr(faceswap, "find_faces", lambda images, progress=None: {
        str(images[0]): [{"box": [40, 40, 120, 140]}, {"box": [260, 50, 340, 150]}]})
    out = tmp_path / "swapped.mp4"
    assert _script().main(["--video", str(_video(tmp_path / "ref.mp4")), "--photo", str(photo),
                           "--ref-frame", "3", "--ref-face", "2", "--ref-frame", "3", "--ref-face", "1",
                           "--out", str(out)]) == 0
    first, second = _calls(fake_ff)
    assert Path(_arg(first, "--source-paths")).name == "face1.png"
    assert Path(_arg(second, "--source-paths")).name == "face2.png"
    assert out.is_file()
    # --out is a file only: nothing in the Library, nothing to check
    assert checks == []
    with SessionLocal() as db:
        assert db.query(MediaItem).filter(MediaItem.kind == "video").count() == 0


def test_a_refused_result_leaves_nothing(client, tmp_path, monkeypatch):
    def refuse(ctx, gen, path, mt):
        path.unlink()
        raise RuntimeError("refused")

    monkeypatch.setattr(content_policy, "check_after", refuse)
    with pytest.raises(RuntimeError):
        faceswap.to_library(_video(tmp_path / "ref.mp4"), _workspace(), "x", {})
    with SessionLocal() as db:
        assert db.query(MediaItem).filter(MediaItem.kind == "video").count() == 0 and db.query(Generation).filter(Generation.kind == "video").count() == 0
