"""Who sings which part: a lead child and a group of children (owner, 7 Nov; the ZIKR Kids sound).

The lyrics say it. `[verse: lead]`, `[chorus: group]` and `[chorus: all]` give a section to one singer, to the
group or to both, and words in (brackets) are the group answering, even inside a lead line. Unmarked sections
follow DEFAULT_ROLE. The markers go to the song model as plain `[verse]`; the roles stay here.

After the song is made, each lyric line is timed on the vocals (app.poetry.timing), and the vocals are cut into
a lead track and a group track with short fades. app.song_voice then sings each track in its own voice.
"""
import re
from pathlib import Path

import numpy as np

ROLES = ("lead", "group", "all")
ALIASES = {"solo": "lead", "everyone": "all", "together": "all", "both": "all", "children": "group",
           "chorus": "group", "kids": "group"}
DEFAULT_ROLE = {"verse": "lead", "bridge": "lead", "pre-chorus": "lead", "hook": "all", "chorus": "all",
                "intro": "all", "outro": "all"}
RATE = 44100
FADE_S = 0.04
PAD_S = 0.08

_MARKER = re.compile(r"^\s*\[\s*([A-Za-z][A-Za-z -]*?)\s*\d*\s*(?::\s*([A-Za-z]+)\s*)?\]\s*$")
_BRACKET = re.compile(r"\(([^()]*)\)")


def role_of(word: str | None, section: str) -> str:
    w = (word or "").strip().lower()
    w = ALIASES.get(w, w)
    return w if w in ROLES else DEFAULT_ROLE.get(section, "lead")


def parse(lyrics: str) -> tuple[str, list[dict]]:
    """(lyrics for the song model, the sung lines with their role). A line is
    {text, sung, section, role, answers: [(start_share, end_share)]}: `sung` is the text without the brackets'
    ( ) marks, and each answer is where its bracketed words fall in the line, as shares of its letters."""
    clean, lines = [], []
    section, role = "verse", DEFAULT_ROLE["verse"]
    for raw in (lyrics or "").splitlines():
        m = _MARKER.match(raw)
        if m:
            section = m[1].strip().lower().replace(" ", "-")
            role = role_of(m[2], section)
            clean.append(f"[{section}]")
            continue
        clean.append(raw)
        text = " ".join(raw.split())
        if not text:
            continue
        sung = _BRACKET.sub(r"\1", text).strip()
        if not sung:
            continue
        answers, at, plain = [], 0, []
        for mt in _BRACKET.finditer(text):
            plain.append(text[at:mt.start()])
            a = len("".join(plain))
            plain.append(mt[1])
            answers.append((a, a + len(mt[1])))
            at = mt.end()
        plain.append(text[at:])
        total = max(1, len("".join(plain)))
        shares = [(a / total, b / total) for a, b in answers]
        # a line that is all brackets, "(Ooh, ooh)", is the group's alone
        line_role = "group" if shares and sum(b - a for a, b in shares) > 0.9 else role
        lines.append({"text": text, "sung": sung, "section": section, "role": line_role, "answers": shares})
    return "\n".join(clean).strip(), lines


def spans(lines: list[dict], duration: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Lead and group stretches of the song from timed lines ({start, end} added by the timing fit)."""
    lead, group = [], []
    for ln in lines:
        a, b = ln.get("start"), ln.get("end")
        if a is None or b is None or b <= a:
            continue
        a, b = max(0.0, a - PAD_S), min(duration, b + PAD_S)
        if ln["role"] in ("lead", "all"):
            lead.append((a, b))
        if ln["role"] in ("group", "all"):
            group.append((a, b))
        if ln["role"] == "lead":
            for s, e in ln.get("answers") or []:
                group.append((a + (b - a) * s, a + (b - a) * e))
    return _merge(lead), _merge(group)


def _merge(items: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(items):
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def envelope(n: int, stretches: list[tuple[float, float]], rate: int = RATE, fade: float = FADE_S) -> np.ndarray:
    """1 inside the stretches, 0 outside, with short linear fades so a cut never clicks."""
    env = np.zeros(n, dtype=np.float64)
    for a, b in stretches:
        i, j = max(0, int(a * rate)), min(n, int(b * rate))
        if j > i:
            env[i:j] = 1.0
    f = max(1, int(fade * rate))
    if f > 1 and n:
        # a running mean over `fade`: every edge becomes a straight ramp of that length, in O(n)
        c = np.concatenate([[0.0], np.cumsum(env)])
        lo = np.clip(np.arange(n) - f // 2, 0, n)
        hi = np.clip(np.arange(n) + f - f // 2, 0, n)
        env = (c[hi] - c[lo]) / np.maximum(hi - lo, 1)
    return env.astype(np.float32)


def split(vocals: np.ndarray, lines: list[dict], duration: float, lead_wav: Path, group_wav: Path,
          rate: int = RATE) -> dict:
    """Writes the lead and group tracks (mono) and says how much of each there is."""
    from app.dub.engines import write_wav

    lead, group = spans(lines, duration)
    write_wav(lead_wav, vocals * envelope(vocals.size, lead, rate), rate)
    write_wav(group_wav, vocals * envelope(vocals.size, group, rate), rate)
    return {"lead_s": round(sum(b - a for a, b in lead), 2), "group_s": round(sum(b - a for a, b in group), 2),
            "lead": lead, "group": group}
