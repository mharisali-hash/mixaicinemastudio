"""Tempo and key of a song, near enough for a remix (M24).

numpy only: spectral flux and its autocorrelation for the tempo, a pitch-class profile matched against the
Krumhansl-Kessler key profiles for the key. Good to a few BPM and usually the right key or its relative,
which is all ACE-Step's bpm / keyscale and a mashup's tempo match need. Cached beside the file like
audio_analysis does.
"""
import json
import os
from pathlib import Path

import numpy as np

from app import audio_analysis as aa

RATE = 11025
HOP = 256
FRAME = 1024
LISTEN_S = 120.0
BPM_RANGE = (70.0, 180.0)
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
CACHE_VERSION = 2


def _frames(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if x.size < frame:
        x = np.pad(x, (0, frame - x.size))
    n = 1 + (x.size - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return x[idx] * np.hanning(frame)[None, :]


def onset_envelope(x: np.ndarray) -> np.ndarray:
    mag = np.abs(np.fft.rfft(_frames(x, FRAME, HOP), axis=1))
    logm = np.log1p(100.0 * mag)
    flux = np.maximum(0.0, np.diff(logm, axis=0)).sum(axis=1)
    if flux.size == 0:
        return flux
    flux -= flux.mean()
    return np.maximum(flux, 0.0)


def tempo(x: np.ndarray) -> float | None:
    env = onset_envelope(x)
    fps = RATE / HOP
    if env.size < fps * 4 or not env.any():
        return None
    ac = np.correlate(env, env, mode="full")[env.size - 1:]
    lo = int(fps * 60 / BPM_RANGE[1])
    hi = min(int(fps * 60 / BPM_RANGE[0]) + 1, ac.size - 1)
    if hi <= lo:
        return None
    lags = np.arange(lo, hi)
    bpm = 60.0 * fps / lags
    # a gentle preference for the 90-130 range, so a half-time feel doesn't win on a tie
    prior = np.exp(-0.5 * (np.log2(bpm / 115.0) / 0.9) ** 2)
    score = ac[lo:hi] * prior
    k = int(np.argmax(score))
    # parabolic interpolation around the peak for a sub-frame lag
    if 0 < k < score.size - 1:
        a, b, c = score[k - 1], score[k], score[k + 1]
        d = (a - 2 * b + c)
        shift = 0.5 * (a - c) / d if d else 0.0
    else:
        shift = 0.0
    return round(60.0 * fps / (lags[k] + shift), 1)


def chroma(x: np.ndarray) -> np.ndarray:
    frame = 4096
    spec = np.abs(np.fft.rfft(_frames(x, frame, frame // 2), axis=1)) ** 2
    freqs = np.fft.rfftfreq(frame, 1.0 / RATE)
    keep = (freqs >= 55.0) & (freqs <= 2000.0)
    pc = (np.round(12 * np.log2(freqs[keep] / 440.0)).astype(int) + 9) % 12
    energy = spec[:, keep].sum(axis=0)
    out = np.zeros(12)
    np.add.at(out, pc, energy)
    return out


def key_of(x: np.ndarray) -> str | None:
    c = chroma(x)
    if not c.any():
        return None
    best, name = -2.0, None
    for root in range(12):
        for profile, mode in ((MAJOR, "major"), (MINOR, "minor")):
            r = float(np.corrcoef(c, np.roll(profile, root))[0, 1])
            if r > best:
                best, name = r, f"{NAMES[root]} {mode}"
    return name


def _tonic(k: str) -> int:
    root, mode = k.split()
    n = NAMES.index(root)
    # a minor key is heard against its relative major
    return (n + 3) % 12 if mode == "minor" else n


def camelot(key: str | None) -> str | None:
    """The DJ wheel code: 8B is C major, 8A its relative A minor; neighbours on the wheel mix well."""
    if not key:
        return None
    num = ((7 * _tonic(key)) % 12 + 7) % 12 + 1
    return f"{num}{'A' if key.endswith('minor') else 'B'}"


def semitones_between(a: str | None, b: str | None, harmonic: bool = False) -> int:
    """How far to move key b to land on key a, the short way round (-6..+5). Relative keys count as the same.
    harmonic=True also accepts a fifth either side (one step on the Camelot wheel), so the smallest shift wins:
    stretching a song's pitch costs more than a key that's merely neighbouring."""
    if not a or not b:
        return 0
    d = (_tonic(a) - _tonic(b)) % 12
    options = (d, d + 7, d - 7) if harmonic else (d,)
    best = [((x + 6) % 12) - 6 for x in options]
    return min(best, key=lambda x: (abs(x), x != best[0]))


def tempo_ratio(target: float | None, bpm: float | None, lo: float = 0.8, hi: float = 1.25) -> float:
    """The atempo factor that brings bpm to target, trying half and double time first; 1.0 when it can't."""
    if not target or not bpm:
        return 1.0
    fits = [r for r in (target / (bpm * m) for m in (0.5, 1.0, 2.0)) if lo <= r <= hi]
    return round(min(fits, key=lambda r: abs(np.log(r))), 4) if fits else 1.0


def _cache(src: Path) -> Path:
    return src.with_name(f"{src.stem}.remix.json")


def analyse(src: Path) -> dict:
    cache = _cache(src)
    try:
        hit = json.loads(cache.read_text())
        if hit.get("v") == CACHE_VERSION and hit.get("mtime") == src.stat().st_mtime_ns:
            return hit["result"]
    except (OSError, ValueError):
        pass
    x = aa._decode_mono(src, RATE)
    duration = x.size / RATE
    if x.size > LISTEN_S * RATE:
        # the middle of the song: intros and outros are often free time
        mid = x.size // 2
        half = int(LISTEN_S * RATE / 2)
        x = x[mid - half: mid + half]
    key = key_of(x)
    result = {"bpm": tempo(x), "key": key, "camelot": camelot(key), "duration_s": round(duration, 3)}
    try:
        tmp = cache.with_name(f".{cache.name}.tmp")
        tmp.write_text(json.dumps({"v": CACHE_VERSION, "mtime": src.stat().st_mtime_ns, "result": result}))
        os.replace(tmp, cache)
    except OSError:
        pass
    return result


def best_offset(lead: np.ndarray, other: np.ndarray, max_s: float) -> float:
    """Seconds to delay `other` so its onsets line up with `lead`'s (0..max_s), from the first 30 s."""
    fps = RATE / HOP
    n = int(30 * RATE)
    a, b = onset_envelope(lead[:n]), onset_envelope(other[:n])
    if a.size == 0 or b.size == 0 or not a.any() or not b.any():
        return 0.0
    best, at = -1.0, 0
    for lag in range(0, int(max_s * fps) + 1):
        m = min(a.size - lag, b.size)
        if m <= 0:
            break
        s = float(np.dot(a[lag:lag + m], b[:m]))
        if s > best:
            best, at = s, lag
    return round(at / fps, 3)


def loudest_window(x: np.ndarray, seconds: float) -> float:
    """Start (s) of the loudest `seconds` of the song: usually its chorus."""
    win = int(seconds * RATE)
    if x.size <= win:
        return 0.0
    hop = RATE // 2
    energy = np.add.reduceat(x.astype(np.float64) ** 2, np.arange(0, x.size, hop))
    k = max(1, win // hop)
    sums = np.convolve(energy, np.ones(k), mode="valid")
    return round(int(np.argmax(sums)) * hop / RATE, 3)
