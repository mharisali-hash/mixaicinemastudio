"""FaceFusion in the facefusion venv: faces from photos swapped into a video, one person per pass.

Job: {repo, target, out, passes: [{source, ref_frame, ref_face}], model, pixel_boost, enhancer, restorer,
quality}. Each pass is one `facefusion.py headless-run` in reference mode: the face at position ref_face
(0-based, left to right) on frame ref_frame is the one replaced, wherever it appears, and nobody else. The next
pass works on the previous one's output. FaceFusion is a separate program here, never imported by the app.
"""
import glob
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time

import runner_io as rio

TICK_S = 15


def cuda_env() -> dict:
    # onnxruntime-gpu's CUDA and cuDNN come as pip wheels (install-facefusion.sh); the loader only finds them
    # when they're on LD_LIBRARY_PATH from the start
    libs = sorted(glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "nvidia", "*", "lib")))
    env = dict(os.environ)
    if libs:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(libs + [p for p in [env.get("LD_LIBRARY_PATH")] if p])
    return env


def command(job: dict, p: dict, target: str, out: str, tmp: str) -> list[str]:
    processors = ["face_swapper"]
    # the restorer puts the original smiles back on the new face; the enhancer goes last so it sharpens that
    if job.get("restorer", True):
        processors.append("expression_restorer")
    if job.get("enhancer", True):
        processors.append("face_enhancer")
    cmd = [sys.executable, "facefusion.py", "headless-run",
           "--source-paths", p["source"], "--target-path", target, "--output-path", out,
           "--temp-path", tmp, "--jobs-path", os.path.join(tmp, "jobs"),
           "--processors", *processors,
           "--face-swapper-model", job.get("model") or "inswapper_128_fp16",
           "--face-swapper-pixel-boost", job.get("pixel_boost") or "512x512",
           "--face-selector-mode", "reference", "--face-selector-order", "left-right",
           "--reference-frame-number", str(int(p["ref_frame"])),
           "--reference-face-position", str(int(p["ref_face"])),
           "--execution-providers", "cuda",
           # FaceFusion's default is 8 threads, each with its own CUDA arena: hyperswap + restorer + enhancer at
           # 8 ran a 20 GB card shared with ComfyUI out of memory (22 Nov). 2 is plenty for a short clip
           "--execution-thread-count", str(job.get("threads") or 2),
           "--video-memory-strategy", "strict",
           "--output-video-quality", str(job.get("quality") or 90)]
    if "expression_restorer" in processors:
        cmd += ["--expression-restorer-model", "live_portrait"]
    if "face_enhancer" in processors:
        cmd += ["--face-enhancer-model", job.get("enhancer_model") or "gfpgan_1.4"]
    return cmd


def run(job: dict) -> dict:
    repo = job["repo"]
    if not os.path.isfile(os.path.join(repo, "facefusion.py")):
        raise RuntimeError(f"FaceFusion isn't checked out at {repo} (scripts/install-facefusion.sh)")
    passes = job.get("passes") or []
    if not passes:
        raise RuntimeError("no faces to swap")
    tmp = tempfile.mkdtemp(prefix="facefusion-")
    env = cuda_env()
    timings = []
    try:
        target = job["target"]
        for i, p in enumerate(passes):
            label = f"Swapping face {i + 1} of {len(passes)}"
            rio.progress(0.05 + 0.9 * i / len(passes), label)
            out = os.path.join(tmp, f"pass{i + 1}{os.path.splitext(job['out'])[1] or '.mp4'}")
            # FaceFusion's progress bar is only on stderr; without our own word every so often the job's
            # heartbeat goes stale on a long video (as with Seed-VC, 7 Nov)
            stop = threading.Event()

            def tick(at=0.05 + 0.9 * (i + 0.5) / len(passes), label=label):
                while not stop.wait(TICK_S):
                    rio.progress(at, label)

            ticker = threading.Thread(target=tick, daemon=True)
            ticker.start()
            t0 = time.monotonic()
            try:
                done = subprocess.run(command(job, p, target, out, tmp), cwd=repo, stdout=sys.stderr,
                                      stderr=sys.stderr, env=env)
            finally:
                stop.set()
                ticker.join(timeout=2)
            timings.append(round(time.monotonic() - t0, 1))
            if not os.path.isfile(out) or os.path.getsize(out) < 1000:
                raise RuntimeError(f"FaceFusion made nothing on pass {i + 1} (exit code {done.returncode})")
            target = out
        os.makedirs(os.path.dirname(job["out"]) or ".", exist_ok=True)
        shutil.move(target, job["out"])
        rio.progress(1.0, "Swapped")
        return {"out": job["out"], "seconds": timings}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    rio.main(run)
