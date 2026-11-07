"""Render an overlay template to transparent WebM with the pinned HyperFrames CLI, and lay overlays on a video."""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from app import subtitles as st
from app.config import REPO_ROOT, get_settings
from app.overlays.words import from_cues

log = logging.getLogger("mixai.overlays")

TEMPLATES = Path(__file__).resolve().parent / "templates"
FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
CAPTION_STYLES = ("classic", "pill", "highlight", "clean")
# what the CLI must never do from a worker: phone home, update itself, pull in packages
QUIET_ENV = {"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1",
             "HYPERFRAMES_NO_AUTO_INSTALL": "1", "HYPERFRAMES_SKIP_SKILLS": "1",
             "HYPERFRAMES_NO_OPTIONAL_INSTALL": "1", "HYPERFRAMES_AGENT": "1"}
# Jafat's gold on dark, the same as the site
COLORS = {"text": "#ffffff", "dim": "#8f8576", "active": "#c8962e", "box": "rgba(250,246,238,0.94)",
          "accent": "#c8962e", "panel": "rgba(18,15,11,0.82)"}
PILL = {**COLORS, "active": "#1c1710"}


class OverlayError(RuntimeError):
    pass


def _dir() -> Path:
    d = get_settings().hyperframes_dir
    return Path(d) if d else REPO_ROOT / "tools" / "hyperframes"


def _cli() -> Path:
    return _dir() / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"


def _gsap() -> Path:
    return _dir() / "node_modules" / "gsap" / "dist" / "gsap.min.js"


def _node() -> str | None:
    return shutil.which(get_settings().node_bin or "node")


def available() -> bool:
    if get_settings().overlays_engine == "classic":
        return False
    return _node() is not None and _cli().is_file() and _gsap().is_file()


def _font(language: str | None, text: str = "", heading: bool = False) -> Path:
    family = st.font_for(language, text)
    if family == st.NASTALIQ:
        return FONTS / "noto-nastaliq-urdu" / "NotoNastaliqUrdu[wght].ttf"
    if family == st.NASKH:
        return FONTS / "noto-naskh-arabic" / "NotoNaskhArabic[wght].ttf"
    if family == st.DEVANAGARI:
        # not bundled; the Noto one the server installs for subtitles
        found = st.font_file(family)
        if found is not None:
            return found
    return FONTS / "poppins" / ("Poppins-ExtraBold.ttf" if heading else "Poppins-Bold.ttf")


def _rtl(language: str | None, text: str) -> bool:
    return st.font_for(language, text) in (st.NASTALIQ, st.NASKH)


def _quote(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def render(template: str, data: dict, *, width: int, height: int, duration: float, fps: int = 30,
           fonts: dict[str, Path] | None = None, files: dict[str, Path] | None = None) -> Path:
    """`template` from templates/, filled with `data`; returns a cached transparent .webm of `duration` s."""
    s = get_settings()
    if not available():
        raise OverlayError("The overlay renderer isn't installed")
    fonts, files = fonts or {}, files or {}
    data = {**data, "width": width, "height": height, "duration": round(duration, 3), "fps": fps}
    stamp = json.dumps([template, data, {k: str(v) for k, v in fonts.items()},
                        {k: str(v) for k, v in files.items()}, (TEMPLATES / template).stat().st_mtime],
                       sort_keys=True, ensure_ascii=False)
    key = hashlib.sha1(stamp.encode()).hexdigest()[:20]
    cache = s.data_dir / "overlays"
    out = cache / f"{key}.webm"
    if out.is_file():
        return out
    work = cache / f".work-{key}"
    shutil.rmtree(work, ignore_errors=True)
    (work / "fonts").mkdir(parents=True)
    try:
        html = (TEMPLATES / template).read_text(encoding="utf-8")
        for k, v in {"__DURATION__": _quote(duration), "__FPS__": str(fps), "__WIDTH__": str(width),
                     "__HEIGHT__": str(height)}.items():
            html = html.replace(k, v)
        # </ can't end the script early: the words are the user's
        blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        html = html.replace("/*DATA*/null", blob)
        (work / "index.html").write_text(html, encoding="utf-8")
        shutil.copyfile(_gsap(), work / "gsap.min.js")
        for name, path in fonts.items():
            shutil.copyfile(path, work / "fonts" / name)
        for name, path in files.items():
            shutil.copyfile(path, work / name)
        part = cache / f".{key}.part.webm"
        env = {**os.environ, **QUIET_ENV}
        if s.hyperframes_browser_path:
            env["HYPERFRAMES_BROWSER_PATH"] = s.hyperframes_browser_path
        ffmpeg = s.ffmpeg_path()
        if ffmpeg:
            env.setdefault("HYPERFRAMES_FFMPEG_PATH", ffmpeg)
        cmd = [_node(), str(_cli()), "render", str(work), "-o", str(part), "--format", "webm", "--fps", str(fps),
               "--quality", "draft", "--workers", str(max(1, s.hyperframes_workers)), "--no-wait"]
        t0 = time.monotonic()
        done = subprocess.run(cmd, cwd=_dir(), env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300 + 20 * duration)
        if done.returncode != 0 or not part.is_file():
            tail = "\n".join((done.stderr or done.stdout or "").strip().splitlines()[-6:])
            raise OverlayError(f"The overlay render failed: {tail or done.returncode}")
        part.replace(out)
        log.info("overlay %s %dx%d %.1fs rendered in %.1fs", template, width, height, duration,
                 time.monotonic() - t0)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compose(video: Path, overlays: list[tuple[Path, float]], dst: Path, encode: list[str] | None = None,
            timeout: int = 7200) -> Path:
    """`overlays` are (transparent webm, start second) pairs, drawn in order over `video`; the sound is copied."""
    ff = get_settings().ffmpeg_path()
    args = [ff, "-y", "-loglevel", "error", "-i", str(video)]
    for path, start in overlays:
        # libvpx-vp9 before the input, or ffmpeg's own decoder drops the alpha channel
        args += ["-itsoffset", _quote(max(0.0, start)), "-c:v", "libvpx-vp9", "-i", str(path)]
    chain, last = [], "[0:v]"
    for n in range(1, len(overlays) + 1):
        chain.append(f"{last}[{n}:v]overlay=0:0:eof_action=pass:format=auto[v{n}]")
        last = f"[v{n}]"
    chain.append(f"{last}format=yuv420p[vout]")
    args += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "0:a?", "-c:a", "copy",
             *(encode or ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]),
             "-movflags", "+faststart", str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if done.returncode != 0:
        raise OverlayError(f"Laying the overlays failed: {(done.stderr or '')[-400:]}")
    return dst


def caption_data(cues, language: str | None, style: str, scale: float = 1.0) -> dict | None:
    words = from_cues(st.as_cues(cues))
    if not words:
        return None
    sample = " ".join(w["text"] for w in words[:40])
    family = st.font_for(language, sample)
    return {"style": style, "words": words, "dir": "rtl" if _rtl(language, sample) else "ltr",
            "tall": family == st.NASTALIQ, "scale": scale, "colors": PILL if style == "pill" else COLORS}


def burn_captions(src: Path, dst: Path, cues, *, style: str = "classic", language: str | None = None,
                  width: int | None = None, height: int | None = None, duration: float | None = None,
                  encode: list[str] | None = None, scale: float = 1.0, timeout: int = 7200, **classic) -> dict:
    """Animated captions in `style`, or the classic burned subtitles when the style is classic, the renderer is
    missing or the render fails. Returns what subtitles.burn returns, plus `style` (and `fallback` when it fell
    back)."""
    def plain(why: str | None = None) -> dict:
        extra = {**classic, **({"scale": scale} if scale != 1.0 else {}), **({"encode": encode} if encode else {})}
        out = st.burn(src, dst, cues, language=language, width=width, height=height, timeout=timeout, **extra)
        return {**out, "style": "classic", **({"fallback": why} if why else {})}

    if style not in CAPTION_STYLES or style == "classic":
        return plain()
    if not available():
        return plain("The animated captions aren't installed on this server, so classic captions were used")
    try:
        from app import brand_render as br

        info = br.video_info(src)
        width, height = width or info.width, height or info.height
        length = duration or getattr(info, "duration", None) or max(float(st.as_cues(cues)[-1].end), 1.0)
        data = caption_data(cues, language, style, scale)
        if data is None:
            shutil.copyfile(src, dst)
            return {"renderer": "none", "fonts_missing": [], "style": style}
        sample = " ".join(w["text"] for w in data["words"][:40])
        fps = min(60, max(12, int(round(getattr(info, "fps", 30) or 30))))
        overlay = render("captions.html", data, width=width, height=height, duration=float(length), fps=fps,
                         fonts={"caption.ttf": _font(language, sample)})
        compose(src, [(overlay, 0.0)], dst, encode=encode, timeout=timeout)
        return {"renderer": "hyperframes", "fonts_missing": [], "style": style}
    except Exception as e:  # noqa: BLE001 - any failure here must still give a captioned video
        log.warning("animated captions failed, using classic: %s", e)
        dst.unlink(missing_ok=True)
        return plain("The animated captions failed, so classic captions were used")


def card(kind: str, *, width: int, height: int, duration: float, language: str | None = None, headline: str = "",
         kicker: str = "", name: str = "", role: str = "", logo: Path | None = None) -> Path:
    """A title card, lower third or logo sting as a transparent .webm."""
    text = " ".join(x for x in (headline, kicker, name, role) if x)
    family = st.font_for(language, text)
    data = {"kind": kind, "headline": headline, "kicker": kicker, "name": name, "role": role,
            "dir": "rtl" if _rtl(language, text) else "ltr", "tall": family == st.NASTALIQ, "colors": COLORS,
            "logo": f"logo{logo.suffix.lower()}" if logo else ""}
    files = {data["logo"]: logo} if logo else {}
    return render("cards.html", data, width=width, height=height, duration=duration,
                  fonts={"head.ttf": _font(language, text, heading=True), "body.ttf": _font(language, text)},
                  files=files)
