"""Picture-and-soundtrack assembly shared by the story videos (app.stories) and Poetry Shorts (app.poetry).

Every part of the timeline becomes a clip of an exact frame count at 30 fps (a still with a slow zoom, or a
video fitted to its slot), the clips cross-fade into each other, the soundtrack goes underneath, then the
captions are burned in, chapters muxed, and an .srt and a small preview copy written next to the video.
"""
from pathlib import Path

from app import audio_edit as ae
from app import subtitles as st
from app.audio_analysis import stream_info
from app.podcast.publish import GROUND, ffmetadata

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
FPS = 30
XFADE = 0.4
ZOOM = 0.06
# a clip a little short of its slot is slowed down to fit rather than frozen on its last frame
MAX_SLOW = 0.15
# page effects (v28d): where welcome / pop start (scale) and how hard they bounce (easeOutBack c1)
EFFECT_FROM = {"welcome": (0.85, 1.70158), "pop": (0.9, 1.3)}
WELCOME_S, POP_S, REVEAL_S = 0.6, 0.4, 0.5
PAD = 1 / min(v[0] for v in EFFECT_FROM.values())
# (size against the usual subtitle size, characters per line)
CAPTIONS = {"16:9": (1.2, 42), "9:16": (1.15, 22), "1:1": (1.15, 30)}
VENC = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def ff(args: list[str], tick=None) -> None:
    ae.run(args, timeout=4 * 3600, tick=tick, loglevel="error")


def _back(start: float, u: str, c1: float) -> str:
    # easeOutBack from `start` to 1.0: overshoots a touch past 1 and settles there at u = 1
    return f"({start}+{1 - start:g}*(1+{c1 + 1:g}*pow({u}-1,3)+{c1:g}*pow({u}-1,2)))"


def still_filter(frames: int, aspect: str, zoom: bool = True, effect: str | None = None, lead: bool = False) -> str:
    """The -vf for a still. `effect` (story pages, v28d): None is the slow zoom; welcome/pop grow the
    picture in from EFFECT_FROM with a bounce; reveal fades in from white; fade holds still. `lead` marks
    the video's first part, which has no cross-fade into it."""
    w, h = SIZES[aspect]
    last = max(1, frames - 1)
    pre, post = "", ""
    if effect in ("welcome", "pop"):
        # zoompan can't go below 1, so the still sits on a GROUND border: at z=1 the picture fills 1/PAD of
        # the frame and z=s*PAD shows it at scale s
        n = round((WELCOME_S if effect == "welcome" else POP_S) * FPS)
        start, c1 = EFFECT_FROM[effect]
        after = f"(1+{ZOOM}*(on-{n})/{max(1, last - n)})" if effect == "welcome" else "1"
        z = f"{PAD:.6f}*if(lt(on,{n}),{_back(start, f'on/{n}', c1)},{after})"
        r, g, b = GROUND
        pre = (f"pad=w=ceil(iw*{PAD:.6f}/2)*2:h=ceil(ih*{PAD:.6f}/2)*2:x=(ow-iw)/2:y=(oh-ih)/2:"
               f"color=0x{r:02x}{g:02x}{b:02x},")
    elif effect in ("reveal", "fade"):
        # fade off the first part only: elsewhere the cross-fade from the part before is the fade
        z, post = "1", clip_fade(effect, lead)
    else:
        z = f"1+{ZOOM}*on/{last}" if zoom else "1"
    return (f"{pre}zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={FPS},"
            f"{post}format=yuv420p")


def clip(still: Path, dst: Path, frames: int, aspect: str, zoom: bool = True, tick=None, effect: str | None = None,
         lead: bool = False) -> None:
    vf = still_filter(frames, aspect, zoom, effect, lead)
    ff(["-loop", "1", "-framerate", str(FPS), "-i", str(still), "-vf", vf, "-frames:v", str(frames),
        *VENC, "-crf", "14", "-an", str(dst)], tick)


def clip_fade(effect: str | None, lead: bool = False) -> str:
    """The fade-in part of a page effect that a video clip still gets; welcome and pop are motion, which a
    clip already has, so they add nothing."""
    if effect == "reveal":
        return f"fade=t=in:st=0:d={REVEAL_S}:color=white,"
    if effect == "fade" and lead:
        return f"fade=t=in:st=0:d={XFADE},"
    return ""


def video_clip(src: Path, dst: Path, frames: int, aspect: str, tick=None, effect: str | None = None,
               lead: bool = False) -> str:
    """A clip made exactly `frames` long: trimmed from the start when longer; when shorter, slowed if up to
    MAX_SLOW covers it, else held on its last frame. Cover-cropped to the frame, its own sound dropped,
    30 fps. No zoom: the clip already moves."""
    w, h = SIZES[aspect]
    want = frames / FPS
    have = stream_info(src).duration or 0.0
    speed, how = 1.0, "trimmed" if have >= want else "held"
    if 0 < have < want <= have * (1 + MAX_SLOW):
        speed, how = want / have, "slowed"
    # tpad also covers the odd frame lost to rounding after a slow-down; -frames:v cuts the rest
    vf = (f"setpts={speed:.6f}*(PTS-STARTPTS),fps={FPS},scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},setsar=1,tpad=stop_mode=clone:stop_duration={want + 1:.3f},{clip_fade(effect, lead)}"
          "format=yuv420p")
    ff(["-i", str(src), "-vf", vf, "-frames:v", str(frames), *VENC, "-crf", "16", "-an", str(dst)], tick)
    return how


def xfade_graph(durs: list[float], audio: int, plan: dict) -> str:
    """Clips 0..n-1 cross-faded XFADE into each other (each clip but the last runs XFADE long, so the
    part starts stay on the soundtrack's clock), and the audio at input `audio` placed after the title."""
    parts, cur = [], "0:v"
    for k in range(1, len(durs)):
        off = sum(durs[:k]) - k * XFADE
        parts.append(f"[{cur}][{k}:v]xfade=transition=fade:duration={XFADE}:offset={off:.4f}[v{k}]")
        cur = f"v{k}"
    parts.append(f"[{cur}]format=yuv420p[vout]")
    a = [f"atrim=end={ae._f(plan['audio_len'])}", "asetpts=PTS-STARTPTS", "aresample=44100"]
    if plan["title_s"]:
        # the title card fades into the first second of sound
        a.append("afade=t=in:d=1")
    if plan["trimmed"]:
        a.append(f"afade=t=out:st={ae._f(max(0.0, plan['audio_len'] - 0.6))}:d=0.6")
    if plan["title_s"]:
        a.append(f"adelay=delays={round(plan['title_s'] * 1000)}:all=1")
    a += [f"apad=whole_dur={ae._f(plan['total'])}", f"atrim=end={ae._f(plan['total'])}"]
    parts.append(f"[{audio}:a]{','.join(a)}[aout]")
    return ";".join(parts)


def slot_frames(seconds: float, last: bool) -> int:
    # every part but the last runs XFADE long so the cross-fade eats into it, not the next part
    return max(2, round((seconds + (0.0 if last else XFADE)) * FPS))


def join(clips: list[Path], durs: list[float], audio: Path, plan: dict, dst: Path, crf: str, tick=None) -> None:
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    ff([*inputs, "-i", str(audio), "-filter_complex", xfade_graph(durs, len(clips), plan),
        "-map", "[vout]", "-map", "[aout]", *VENC, "-crf", crf, "-c:a", "aac", "-b:a", "192k",
        "-t", ae._f(plan["total"]), str(dst)], tick=tick)


def finish(joined: Path, work: Path, dst: Path, *, aspect: str, language: str | None, cues: list, title: str,
           chapters: list[dict], srt: Path | None = None, srt_cues: list | None = None, preview: Path | None = None,
           fade: float = 0.0, progress=None, caption_style: str = "classic") -> dict | None:
    """Captions burned into the joined cut, chapters muxed, `dst` written, then the .srt and the preview.
    Returns what subtitles.burn said (None when there were no captions to burn)."""
    say = progress or (lambda f, m: None)
    w, h = SIZES[aspect]
    burned = None
    if cues:
        say(0.85, "Burning in the captions")
        scale, chars = CAPTIONS[aspect]
        from app import overlays

        # an animated style falls back to these same burned captions if it can't be drawn
        burned = overlays.burn_captions(joined, work / "captioned.mp4", cues, style=caption_style, language=language,
                                        width=w, height=h, timeout=4 * 3600, encode=[*VENC, "-crf", "20"],
                                        scale=scale, chars=chars, fade=fade)
        joined = work / "captioned.mp4"
    say(0.96, "Adding the chapters")
    meta = work / "chapters.txt"
    meta.write_text(ffmetadata({"title": title or ""}, chapters), encoding="utf-8")
    part = work / "final.mp4"
    ff(["-i", str(joined), "-i", str(meta), "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy",
        "-movflags", "+faststart", str(part)])
    part.replace(dst)
    if srt is not None:
        st.write_srt(srt, cues if srt_cues is None else srt_cues, language)
    if preview is not None:
        # a small copy to watch over a slow link before downloading the full one (~0.5 Mbit/s)
        say(0.98, "Making the preview")
        pw, ph = (640, 360) if w > h else (360, 640) if h > w else (480, 480)
        ff(["-i", str(dst), "-vf", f"scale={pw}:{ph}:flags=lanczos,format=yuv420p", "-c:v", "libx264",
            "-preset", "veryfast", "-b:v", "430k", "-maxrate", "500k", "-bufsize", "1000k", "-c:a", "aac",
            "-b:a", "64k", "-movflags", "+faststart", str(preview)])
    return burned
