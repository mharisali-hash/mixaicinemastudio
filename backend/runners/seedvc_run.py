"""Runs Seed-VC's inference.py with its output written by soundfile instead of torchaudio.

torchaudio 2.7 loads FFmpeg to save a .wav; against the server's FFmpeg 8 that crashes half way through the
file (a header and no audio). Usage, from the Seed-VC checkout: python seedvc_run.py inference.py <its args>
"""
import os
import runpy
import sys

import soundfile as sf
import torchaudio


def _save(path, wav, sample_rate, *args, **kwargs):
    x = wav.detach().cpu().numpy() if hasattr(wav, "detach") else wav
    if getattr(x, "ndim", 1) == 2:
        x = x.T  # torchaudio is (channels, frames); soundfile wants (frames, channels)
    sf.write(path, x, int(sample_rate), subtype="PCM_16")


torchaudio.save = _save
# the checkout's own packages (modules/, hf_utils) are found from the working directory
sys.path.insert(0, os.getcwd())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
