"""Seed-VC in the seed-vc venv: a song's vocals sung again in another voice.

Job: {source, target, out, repo, steps}. Runs the checkout's own inference.py with the f0-conditioned 44 kHz
singing model (it downloads that checkpoint the first time) and moves its one output file to `out`.
Seed-VC is GPL-3.0, so it stays a separate program here rather than being imported into the app.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import runner_io as rio


def run(job: dict) -> dict:
    repo = job["repo"]
    if not os.path.isfile(os.path.join(repo, "inference.py")):
        raise RuntimeError(f"Seed-VC isn't checked out at {repo} (scripts/install-song-voice.sh)")
    tmp = tempfile.mkdtemp(prefix="seedvc-")
    try:
        rio.progress(0.05, "Loading the singing voice model")
        # seedvc_run.py writes the result with soundfile; torchaudio's FFmpeg writer crashes on the server
        shim = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seedvc_run.py")
        cmd = [sys.executable, shim, "inference.py", "--source", job["source"], "--target", job["target"],
               "--output", tmp, "--diffusion-steps", str(job.get("steps") or 30), "--length-adjust", "1.0",
               "--inference-cfg-rate", "0.7", "--f0-condition", "True", "--auto-f0-adjust", "False",
               "--semi-tone-shift", str(job.get("shift") or 0), "--fp16", "True"]
        env = {**os.environ, "TORIO_USE_FFMPEG": "0"}
        # its chatter goes to our stderr, which the worker keeps for the error message
        done = subprocess.run(cmd, cwd=repo, stdout=sys.stderr, stderr=sys.stderr, env=env)
        made = sorted(glob.glob(os.path.join(tmp, "*.wav")), key=os.path.getmtime)
        if not made:
            raise RuntimeError(f"Seed-VC wrote no audio (exit code {done.returncode})")
        import soundfile as sf

        # a crash half way through leaves a header and no audio; that is a failure, not a song
        try:
            frames = sf.info(made[-1]).frames
        except Exception:  # noqa: BLE001
            frames = 0
        if frames < 1000:
            raise RuntimeError(f"Seed-VC's output is empty or broken (exit code {done.returncode})")
        os.makedirs(os.path.dirname(job["out"]) or ".", exist_ok=True)
        shutil.move(made[-1], job["out"])
        rio.progress(1.0, "Sung")
        return {"out": job["out"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    rio.main(run)
