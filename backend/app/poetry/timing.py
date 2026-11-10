"""When each line of the poem is sung (contract v33).

The `poetry_timing` job (audio lane) splits the vocals off the song, finds the stretches where someone is
singing (frame energy against the track's own floor), and asks Whisper for word times. The lines are then
laid onto that, in order:

- whisper: the heard words line up with the poem well enough, so each line starts and ends on its words;
- phrases: line boundaries go into the pauses between sung phrases, chosen so each line gets a share of the
  sung time in proportion to its length (several short phrases merge into one line, a long phrase is split);
- even: no pause to go on, so the lines share the sung time equally.

The fitting is plain functions over (start, end) regions, so the editor's numbers and the tests need no audio.
"""
import logging
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from app import aerab
from app import audio_analysis as aa
from app.config import get_settings
from app.models import Generation
from app.services import generation_file
from app.workdirs import job_workdir

log = logging.getLogger("mixai.poetry")

JOB = "poetry_timing"
RATE = 16000
FRAME_S = 0.05
MIN_GAP_S = 0.3
MIN_PHRASE_S = 0.25
MIN_LINE_S = 0.5
# how far (in sung seconds) a pause may sit from where a line "should" end and still be used, per line length
SNAP_SHARE = 0.6
WHISPER_MIN_RATIO = 0.5
WHISPER_MIN_LINES = 0.7

Region = tuple[float, float]


class TimingError(RuntimeError):
    pass


# ---------------------------------------------------------------- listening

def voiced_regions(samples: np.ndarray, rate: int = RATE, *, min_gap: float = MIN_GAP_S,
                   min_len: float = MIN_PHRASE_S) -> list[Region]:
    """Where the (vocal) track is sounding. A track that never dips is one long phrase; silence is none."""
    n = int(rate * FRAME_S)
    k = len(samples) // n if n else 0
    if k == 0:
        return []
    frames = samples[: k * n].astype(np.float64).reshape(k, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    peak, floor = float(np.percentile(db, 95)), float(np.percentile(db, 10))
    if peak < -50:
        return []
    if peak - floor < 8:
        return [(0.0, round(k * FRAME_S, 3))]
    on = db > max(floor + 0.3 * (peak - floor), peak - 35)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
    runs = [[a * FRAME_S, b * FRAME_S] for a, b in zip(edges[::2], edges[1::2])]
    merged: list[list[float]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] < min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(round(float(a), 3), round(float(b), 3)) for a, b in merged if b - a >= min_len]


def weight(text: str) -> int:
    """Roughly how long a line takes to sing: its letters, aerab left out."""
    return max(1, sum(1 for ch in aerab.strip(text) if ch.isalpha()))


# ---------------------------------------------------------------- fitting

def _clock(regions: list[Region]) -> list[float]:
    # sung time: the regions laid end to end with the pauses taken out
    cum = [0.0]
    for a, b in regions:
        cum.append(cum[-1] + (b - a))
    return cum


def _real(regions: list[Region], cum: list[float], v: float) -> float:
    for i, (a, b) in enumerate(regions):
        if v <= cum[i + 1] + 1e-9:
            return a + max(0.0, v - cum[i])
    return regions[-1][1]


def _best_gaps(gv: list[float], targets: list[float]) -> list[int]:
    """One pause per target, in order, as close to the targets as possible (len(gv) >= len(targets))."""
    m, g = len(targets), len(gv)
    inf = float("inf")
    cost = [[inf] * g for _ in range(m)]
    back = [[-1] * g for _ in range(m)]
    for j in range(g):
        cost[0][j] = abs(gv[j] - targets[0])
    for k in range(1, m):
        best, arg = inf, -1
        for j in range(g):
            if j > 0 and cost[k - 1][j - 1] < best:
                best, arg = cost[k - 1][j - 1], j - 1
            if arg >= 0:
                cost[k][j] = best + abs(gv[j] - targets[k])
                back[k][j] = arg
    j = min(range(g), key=lambda x: cost[m - 1][x])
    out = [j]
    for k in range(m - 1, 0, -1):
        j = back[k][j]
        out.append(j)
    return out[::-1]


def fit_phrases(weights: list[int], regions: list[Region]) -> tuple[list[Region], str]:
    """Lines onto sung regions, in order. Returns the spans and "phrases" or "even"."""
    n = len(weights)
    cum = _clock(regions)
    total = cum[-1]
    gaps = list(range(len(regions) - 1))  # gap j sits between region j and j + 1
    gv = [cum[j + 1] for j in gaps]

    def anchored(ws: list[int]) -> dict[int, int]:
        wc = np.cumsum([0, *ws]).tolist()
        targets = [total * wc[k] / wc[-1] for k in range(1, n)]
        if not targets or not gaps:
            return {}
        if len(gaps) >= len(targets):
            return {k + 1: j for k, j in enumerate(_best_gaps(gv, targets))}
        # too few pauses for every line: use the ones that land near a line end, split the rest by length
        tol = SNAP_SHARE * total / n
        out, last = {}, 0
        for j in gaps:
            ks = [k for k in range(last + 1, n) if abs(gv[j] - targets[k - 1]) <= tol]
            if ks:
                k = min(ks, key=lambda k: abs(gv[j] - targets[k - 1]))
                out[k], last = j, k
        return out

    pinned = anchored(weights)
    source = "phrases"
    if not pinned:
        weights, source = [1] * n, "even"
    wc = np.cumsum([0, *weights]).tolist()
    # cut k sits between line k-1 and line k; pinned cuts are pauses, the rest are spread by length between them
    anchors = [(0, 0.0)] + sorted((k, gv[j]) for k, j in pinned.items()) + [(n, total)]
    pos: dict[int, float] = {}
    for (k1, p1), (k2, p2) in zip(anchors, anchors[1:]):
        for k in range(k1 + 1, k2):
            pos[k] = p1 + (p2 - p1) * (wc[k] - wc[k1]) / max(1e-9, wc[k2] - wc[k1])
    spans, start = [], regions[0][0]
    for k in range(1, n):
        if k in pinned:
            j = pinned[k]
            spans.append((start, regions[j][1]))
            start = regions[j + 1][0]
        else:
            t = _real(regions, cum, pos[k])
            spans.append((start, t))
            start = t
    spans.append((start, regions[-1][1]))
    return spans, source


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", aerab.strip(word).lower())


def from_words(texts: list[str], words: list[dict]) -> list[Region] | None:
    """Each line on its heard words, when Whisper heard the poem closely enough; None when it didn't."""
    expected = [(i, _norm(w)) for i, t in enumerate(texts) for w in t.split()]
    expected = [(i, w) for i, w in expected if w]
    heard = [(float(w["start"]), float(w["end"]), _norm(str(w.get("w") or w.get("word") or ""))) for w in words]
    heard = [h for h in heard if h[2]]
    if not expected or not heard:
        return None
    sm = SequenceMatcher(None, [w for _, w in expected], [h[2] for h in heard], autojunk=False)
    if sm.ratio() < WHISPER_MIN_RATIO:
        return None
    hits: dict[int, list[tuple[float, float]]] = {}
    for blk in sm.get_matching_blocks():
        for d in range(blk.size):
            line = expected[blk.a + d][0]
            hits.setdefault(line, []).append(heard[blk.b + d][:2])
    n = len(texts)
    if len(hits) < WHISPER_MIN_LINES * n:
        return None
    spans: list[Region | None] = [(min(a for a, _ in hits[i]), max(b for _, b in hits[i])) if i in hits else None
                                  for i in range(n)]
    # lines Whisper missed share the time between their neighbours; a miss at either end can't be placed
    i = 0
    while i < n:
        if spans[i] is not None:
            i += 1
            continue
        j = i
        while j < n and spans[j] is None:
            j += 1
        if i == 0 or j == n:
            return None
        prev, nxt = spans[i - 1][1], spans[j][0]
        if nxt <= prev:
            return None
        share = (nxt - prev) / (j - i)
        for m, k in enumerate(range(i, j)):
            spans[k] = (prev + m * share, prev + (m + 1) * share)
        i = j
    return spans


def tidy(spans: list[Region], duration: float) -> list[dict]:
    """In order, inside the song, none shorter than MIN_LINE_S where there's room."""
    out = []
    for i, (a, b) in enumerate(spans):
        a = max(0.0, min(a, duration - 0.05))
        if out:
            a = max(a, out[-1]["end"])
        room = spans[i + 1][0] if i + 1 < len(spans) else duration
        # a too-short line borrows from the pause after it, never from the next line
        b = max(b, min(a + MIN_LINE_S, max(room, b)))
        b = max(min(b, duration), a + 0.05)
        out.append({"start": round(float(a), 3), "end": round(float(b), 3)})
    return out


def fit(texts: list[str], regions: list[Region], duration: float, words: list[dict] | None = None) -> dict:
    if not texts:
        return {"lines": [], "source": "even"}
    if words:
        got = from_words(texts, words)
        if got is not None:
            return {"lines": tidy(got, duration), "source": "whisper"}
    regions = [r for r in regions if r[1] > r[0]] or [(0.0, duration)]
    spans, source = fit_phrases([weight(t) for t in texts], regions)
    return {"lines": tidy(spans, duration), "source": source}


def check_edit(rows: list[dict], duration: float | None) -> list[dict]:
    """An edited timing list, or ValueError saying what's wrong with it."""
    out, prev_end = [], 0.0
    for n, r in enumerate(rows, 1):
        a, b = round(float(r["start"]), 3), round(float(r["end"]), 3)
        if b <= a:
            raise ValueError(f"Line {n} ends before it starts")
        if a < prev_end - 1e-3:
            raise ValueError(f"Line {n} starts before line {n - 1} ends")
        if duration and b > duration + 0.05:
            raise ValueError(f"Line {n} ends after the song ({duration:.1f} s)")
        out.append({"start": a, "end": b})
        prev_end = b
    return out


# ---------------------------------------------------------------- the job

def _w():
    from app import worker

    return worker


def _words(res: dict) -> list[dict]:
    return [w for s in res.get("segments") or [] for w in s.get("words") or []]


def handle(ctx) -> dict:
    from app.dub import engines

    db, job = ctx.db, ctx.job
    p = job.payload or {}
    g = db.get(Generation, p.get("song_generation_id") or "")
    src = generation_file(g) if g is not None else None
    if src is None or not src.is_file():
        raise TimingError("The song is gone; make it again")
    W = _w()
    work = job_workdir("poetry", job.id)
    texts = list(p.get("lines") or [])
    try:
        ctx.progress(0.02, "Reading the song")
        wav = work / "song.wav"
        engines._ffmpeg(["-i", str(src), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)])
        duration = aa.stream_info(wav).duration
        driver = ctx.driver_factory()
        vocals, separated = wav, False
        try:
            res = driver.separate(wav, work / "stems", get_settings().dub_separation_model,
                                  lambda f, m: ctx.progress(0.05 + 0.5 * f, "Separating the vocals"))
            if res.get("vocals") and Path(res["vocals"]).is_file():
                vocals, separated = Path(res["vocals"]), True
        except (W.JobCancelled, W.WorkerStopping):
            raise
        except Exception as e:
            # the whole mix still has the singing in it; louder parts are usually the voice anyway
            log.warning("vocal split failed for %s, timing on the mix: %s", job.id, e)
        ctx.progress(0.6, "Listening for the phrases")
        regions = voiced_regions(aa._decode_mono(vocals, RATE))
        words: list[dict] = []
        if p.get("whisper", True) and texts:
            try:
                words = _words(driver.transcribe(vocals, p.get("language"),
                                                 lambda f, m: ctx.progress(0.62 + 0.3 * f, "Listening for the words")))
            except (W.JobCancelled, W.WorkerStopping):
                raise
            except Exception as e:
                log.warning("whisper failed for %s, fitting on the phrases: %s", job.id, e)
        ctx.progress(0.95, "Fitting the lines")
        out = fit(texts, regions, duration, words)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**out, "phrases": [list(r) for r in regions[:500]], "duration_s": round(duration, 3),
            "separated": separated, "heard_words": len(words)}
