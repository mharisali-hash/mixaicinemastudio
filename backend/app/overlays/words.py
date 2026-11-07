"""Word timings for animated captions. A cue that carries its own words (WhisperX) keeps them; otherwise the
cue's time is shared out by the length of each word, which is close enough for a word that lights up."""
import re

_SPLIT = re.compile(r"\s+")


def _get(c, key, default=None):
    return c.get(key, default) if isinstance(c, dict) else getattr(c, key, default)


def from_cues(cues) -> list[dict]:
    out: list[dict] = []
    for n, c in enumerate(cues or []):
        start, end = float(_get(c, "start", 0) or 0), float(_get(c, "end", 0) or 0)
        heard = _get(c, "words") or []
        if heard:
            for w in heard:
                text = (w.get("word") or w.get("text") or "").strip()
                if text:
                    out.append({"text": text, "start": round(float(w["start"]), 3),
                                "end": round(float(w["end"]), 3), "cue": n})
            continue
        words = [w for w in _SPLIT.split((_get(c, "text") or "").strip()) if w]
        if not words or end <= start:
            continue
        # a long word takes longer to say; +1 so short words still get a moment
        weights = [len(w) + 1 for w in words]
        total, t = sum(weights), start
        for w, k in zip(words, weights):
            span = (end - start) * k / total
            out.append({"text": w, "start": round(t, 3), "end": round(t + span, 3), "cue": n})
            t += span
    return out
