"""Placeholder driver so the whole review loop works on a laptop with no GPU."""
import json
import random
import struct
import subprocess
import textwrap
import time
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings
from app.drivers.base import DriverResult, ProgressCallback

ASPECTS = {"16:9": (1024, 576), "9:16": (576, 1024), "1:1": (768, 768), "4:3": (1024, 768), "2.39:1": (1224, 512)}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without scalable default font
        return ImageFont.load_default()


def _size(params: dict) -> tuple[int, int]:
    if params.get("width") and params.get("height"):
        return max(64, int(params["width"])), max(64, int(params["height"]))
    # character art is portrait-shaped regardless of the film's aspect ratio
    if params.get("kind") in ("portrait", "sheet_view"):
        return 768, 1024
    return ASPECTS.get(params.get("aspect_ratio", "16:9"), ASPECTS["16:9"])


def render_placeholder(prompt: str, params: dict, seed: int, out_path: Path) -> Path:
    w, h = _size(params)
    rng = random.Random(seed)
    # seed picks the hue so different versions are visibly different at a glance
    # warm sepia range to sit with the parchment UI
    r = rng.randint(60, 110)
    top = (r, int(r * rng.uniform(0.6, 0.75)), int(r * rng.uniform(0.3, 0.45)))
    bottom = (rng.randint(14, 24), rng.randint(9, 16), rng.randint(5, 10))

    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))

    pad = max(16, w // 24)
    body_px = max(12, w // 44)
    small_px = max(10, w // 60)
    title = _font(max(14, w // 28))
    body = _font(body_px)
    small = _font(small_px)

    draw.text((pad, pad), "MOCK · " + str(params.get("kind", "image")).upper(), font=title, fill=(235, 200, 120))
    chars_per_line = max(20, int((w - 2 * pad) / (body_px * 0.55)))
    lines = textwrap.wrap(prompt.strip() or "(empty prompt)", width=chars_per_line)[:10]
    draw.multiline_text((pad, pad * 3), "\n".join(lines), font=body, fill=(240, 230, 214), spacing=6)
    footer = f"v{params.get('version', 1)}  ·  seed {seed}  ·  {w}x{h}"
    draw.text((pad, h - pad - small_px), footer, font=small, fill=(196, 174, 140))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def placeholder_glb(seed: int) -> bytes:
    """A small coloured cube as a real binary glTF, so the 3D viewer and the exporters have something to open."""
    rng = random.Random(seed)
    # 24 vertices (4 per face) so each face keeps its own flat normal
    faces = [((1, 0, 0), (0, 1, 0)), ((-1, 0, 0), (0, 1, 0)), ((0, 1, 0), (0, 0, 1)),
             ((0, -1, 0), (0, 0, 1)), ((0, 0, 1), (0, 1, 0)), ((0, 0, -1), (0, 1, 0))]
    pos, nrm, idx = [], [], []
    for n, up in faces:
        side = (up[1] * n[2] - up[2] * n[1], up[2] * n[0] - up[0] * n[2], up[0] * n[1] - up[1] * n[0])
        base = len(pos)
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            pos.append(tuple(0.5 * (n[i] + a * side[i] + b * up[i]) for i in range(3)))
            nrm.append(n)
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    blob = b"".join(struct.pack("<3f", *p) for p in pos) + b"".join(struct.pack("<3f", *v) for v in nrm)
    blob += struct.pack(f"<{len(idx)}H", *idx)
    blob += b"\0" * (-len(blob) % 4)
    vec = 12 * len(pos)
    colour = [round(rng.uniform(0.45, 0.8), 3), round(rng.uniform(0.3, 0.5), 3), round(rng.uniform(0.15, 0.3), 3), 1]
    doc = {
        "asset": {"version": "2.0", "generator": "mixai mock"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "Mock model"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": colour, "metallicFactor": 0.1,
                                                "roughnessFactor": 0.7}}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": vec, "target": 34962},
                        {"buffer": 0, "byteOffset": vec, "byteLength": vec, "target": 34962},
                        {"buffer": 0, "byteOffset": 2 * vec, "byteLength": 2 * len(idx), "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                       "min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]},
                      {"bufferView": 1, "componentType": 5126, "count": len(nrm), "type": "VEC3"},
                      {"bufferView": 2, "componentType": 5123, "count": len(idx), "type": "SCALAR"}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\0") + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class MockDriver:
    name = "mock"

    def __init__(self, step_seconds: float | None = None, steps: int = 6):
        # None = realistic 0.5–1.0 s per step (3–6 s total); tests pass 0
        self.step_seconds = step_seconds
        self.steps = steps

    def _tick(self, progress_cb: ProgressCallback, label: str, upto: float = 0.9) -> None:
        for i in range(self.steps):
            delay = self.step_seconds if self.step_seconds is not None else random.uniform(0.5, 1.0)
            if delay:
                time.sleep(delay)
            progress_cb(upto * (i + 1) / self.steps, f"{label} {i + 1}/{self.steps}")

    def generate_image(self, prompt, params, seed, out_path: Path, progress_cb) -> DriverResult:
        if params.get("restore"):
            return self._restore(params["restore"], out_path, progress_cb)
        if params.get("upscale"):
            return self._upscale_image(params["upscale"], out_path, progress_cb)
        self._tick(progress_cb, "Sampling")
        render_placeholder(prompt, params, seed, out_path)
        progress_cb(1.0, "Done")
        meta = {"width": _size(params)[0], "height": _size(params)[1]}
        if params.get("model"):
            meta["model"] = params["model"]  # whatever the catalog picked; the placeholder looks the same
            from app import models_catalog as mc

            m = mc.MODELS.get(params["model"])
            if m is not None and m.type in ("image", "edit") and not params.get("reference_ids"):
                # the graph the comfy driver would run, so the result tile and estimates see the same key
                meta["template"] = mc.template_for(m, params.get("speed"), i2i=bool(params.get("img2img_of")))
        if params.get("image_tool"):
            # an image tool: the placeholder at the tool's working size, with the template the comfy driver would use
            meta["template"] = params["image_tool"]["template"]
        return DriverResult(out_path, "image/png", meta)

    def generate_model(self, prompt, params, seed, out_path: Path, progress_cb) -> DriverResult:
        rec = params.get("model3d") or {}
        self._tick(progress_cb, "Shaping")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(placeholder_glb(seed))
        progress_cb(1.0, "Done")
        from app import model3d

        return DriverResult(out_path, "model/gltf-binary", {"template": model3d.template_of(rec),
                                                            "engine": rec.get("engine"), "quality": rec.get("quality")})

    def generate_video(self, prompt, params, seed, out_path: Path, progress_cb) -> DriverResult:
        if params.get("kind") == "upscale_segment":
            return self._upscale(params, out_path, progress_cb)
        self._tick(progress_cb, "Rendering", upto=0.8)
        frame = out_path.with_suffix(".frame.png")
        render_placeholder(prompt, params, seed, frame)
        seconds = float(params.get("duration_s", 2))
        cmd = [get_settings().ffmpeg_path(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(frame)]
        if params.get("speech_audio"):
            # a lip-sync take (ltx23_ia2v): the 8n+1 frames it asked for, with the speech window it was given
            frames = int(params.get("num_frames") or round(seconds * 24) + 1)
            seconds = frames / 24
            cmd += ["-ss", f"{float(params.get('audio_start_s') or 0):.4f}", "-t", f"{seconds:.4f}",
                    "-i", str(params["speech_audio"]), "-af", f"apad=whole_dur={seconds:.4f}",
                    "-frames:v", str(frames), "-c:a", "aac"]
        elif params.get("kind") == "take_chunk":
            # long-take chunks: exact 8n+1 frame count plus a tone, so the joiner has audio to crossfade
            frames = int(params["num_frames"])
            seconds = frames / 24
            cmd += ["-f", "lavfi", "-t", f"{seconds:.4f}", "-i", f"sine=frequency={220 + seed % 400}:sample_rate=48000",
                    "-frames:v", str(frames), "-c:a", "aac"]
        else:
            cmd += ["-t", f"{seconds:.2f}"]
        cmd += ["-r", "24", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(out_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace')[-400:]}") from e
        finally:
            frame.unlink(missing_ok=True)
        progress_cb(1.0, "Done")
        meta = {"duration_s": seconds}
        if params.get("kind") == "video":
            meta.update(model=params.get("model"), width=_size(params)[0], height=_size(params)[1])
        return DriverResult(out_path, "video/mp4", meta)

    def _upscale(self, params, out_path: Path, progress_cb) -> DriverResult:
        # plain lanczos, video only, same frames: what the real engines return minus the detail
        self._tick(progress_cb, "Upscaling", upto=0.8)
        w, h = int(params["out_width"]), int(params["out_height"])
        cmd = [get_settings().ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(params["video_path"]),
               "-vf", f"scale={w}:{h}:flags=lanczos,setsar=1", "-an", "-fps_mode", "passthrough",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", str(out_path)]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace')[-400:]}") from e
        progress_cb(1.0, "Done")
        return DriverResult(out_path, "video/mp4", {"width": w, "height": h, "template": params.get("template")})

    def _upscale_image(self, up: dict, out_path: Path, progress_cb) -> DriverResult:
        # lanczos only, but at the exact size the real templates return
        self._tick(progress_cb, "Upscaling")
        w, h = int(up["width"]), int(up["height"])
        with Image.open(get_settings().data_dir / up["source_file"]) as im:
            big = im.convert("RGB").resize((w, h), Image.LANCZOS)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        big.save(out_path, "PNG", compress_level=1)
        progress_cb(1.0, "Done")
        return DriverResult(out_path, "image/png", {"width": w, "height": h, "template": up.get("template")})

    def _restore(self, r: dict, out_path: Path, progress_cb) -> DriverResult:
        # what each real template returns, near enough: masks are a centred oval, edits change one patch
        self._tick(progress_cb, "Restoring")
        w, h = r["proc"]
        with Image.open(get_settings().data_dir / r["source_file"]) as im:
            img = im.convert("RGB").resize((w, h), Image.LANCZOS)
        if r["template"] in ("photo_cutout_mask", "photo_sam_mask"):
            img = Image.new("RGB", (w, h), 0)
            ImageDraw.Draw(img).ellipse((w * 0.25, h * 0.15, w * 0.75, h * 0.9), fill=(255, 255, 255))
        elif r["template"] == "photo_colourise":
            img = Image.merge("RGB", [c.point(lambda v, k=k: min(255, int(v * k))) for c, k in
                                      zip(img.convert("L").convert("RGB").split(), (1.08, 1.0, 0.86))])
        elif r.get("tool") in ("fix", "scratches", "background", "stylise"):
            ImageDraw.Draw(img).rectangle((w * 0.1, h * 0.1, w * 0.3, h * 0.3), fill=(200, 60, 40))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG", compress_level=1)
        progress_cb(1.0, "Done")
        return DriverResult(out_path, "image/png", {"width": w, "height": h, "template": r["template"]})

    def _speech_seconds(self, params) -> float:
        if params.get("duration_s"):
            return float(params["duration_s"])
        from app import voices

        e = voices.ENGINES.get(params.get("engine") or "", voices.default())
        segs = voices.segments(params.get("text") or "", e)
        words = sum(len(str(v).split()) for k, v in segs if k == "text")
        pauses = sum(float(v) for k, v in segs if k == "pause")
        # about 2.5 words a second at speed 1
        return words / 2.5 / float(params.get("speed") or 1.0) + pauses

    def transcribe(self, path: Path, language, progress_cb) -> dict:
        # one line every 2.5 s of the clip, with evenly spaced word timings, so the transcript and dub
        # editors have real-looking segments to show
        from app.audio_analysis import stream_info

        self._tick(progress_cb, "Transcribing")
        total = stream_info(path).duration or 2.0
        lines = ["This is a mock transcript of the reference clip."]
        segs, t, n = [], 0.0, 1
        while t < total - 0.5 or not segs:
            end = min(total, t + 2.2) if total > 2.0 else 2.0
            text = lines[0] if n == 1 else f"Mock line {n} of the transcript."
            words = text.split()
            step = (end - t) / len(words)
            segs.append({"start": round(t, 3), "end": round(end, 3), "text": text,
                         "words": [{"start": round(t + i * step, 3), "end": round(t + (i + 1) * step, 3), "word": w}
                                   for i, w in enumerate(words)]})
            t, n = t + 2.5, n + 1
        return {"text": " ".join(s["text"] for s in segs), "language": language or "en", "segments": segs,
                "duration_s": total}

    # ---- Dub a video (contract v18): the same calls ComfyDriver sends to app.dub.engines

    def separate(self, src: Path, out_dir: Path, model: str, progress_cb) -> dict:
        # "vocals" is the mix itself, the "instrumental" a quiet bed of noise the same length
        from app.audio_analysis import stream_info

        self._tick(progress_cb, "Separating")
        out_dir.mkdir(parents=True, exist_ok=True)
        vocals, bed = out_dir / "mock_vocals.wav", out_dir / "mock_instrumental.wav"
        ff = get_settings().ffmpeg_path()
        seconds = stream_info(src).duration or 1.0
        for cmd in ([ff, "-y", "-loglevel", "error", "-i", str(src), "-ac", "2", "-ar", "48000", str(vocals)],
                    [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                     f"anoisesrc=d={seconds:.3f}:c=pink:r=48000:a=0.03", "-ac", "2", str(bed)]):
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        progress_cb(1.0, "Done")
        return {"vocals": vocals, "instrumental": bed}

    def separate_stems(self, src: Path, out_dir: Path, model: str, progress_cb) -> dict:
        # Stems (contract v21), four of them: vocals is the mix itself, the rest are made up the same length
        # (a kick-like pulse, a low hum and a quiet bed) so each tile has a different waveform
        from app.audio_analysis import stream_info

        self._tick(progress_cb, "Separating the stems")
        out_dir.mkdir(parents=True, exist_ok=True)
        ff = get_settings().ffmpeg_path()
        seconds = stream_info(src).duration or 1.0
        fake = {"drums": f"aevalsrc=0.5*sin(2*PI*60*t)*lt(mod(t\\,0.5)\\,0.08):s=48000:d={seconds:.3f}",
                "bass": f"aevalsrc=0.3*sin(2*PI*55*t):s=48000:d={seconds:.3f}",
                "other": f"anoisesrc=d={seconds:.3f}:c=pink:r=48000:a=0.03"}
        out = {"vocals": out_dir / "mock_(Vocals).wav"}
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-ac", "2", "-ar", "48000", str(out["vocals"])],
                       check=True, capture_output=True, timeout=120)
        for name, graph in fake.items():
            out[name] = out_dir / f"mock_({name.title()}).wav"
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", graph, "-ac", "2", str(out[name])],
                           check=True, capture_output=True, timeout=120)
        progress_cb(1.0, "Done")
        return out

    def convert_singing(self, src: Path, ref: Path, out: Path, progress_cb) -> Path:
        # the "new voice" is the vocals a fifth up, so a test can hear that something changed
        self._tick(progress_cb, "Singing it again")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([get_settings().ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(src), "-af",
                        "asetrate=72000,aresample=48000", "-ac", "1", str(out)],
                       check=True, capture_output=True, timeout=120)
        progress_cb(1.0, "Done")
        return out

    def speaker_embed(self, src: Path, spans, progress_cb) -> list[list[float]]:
        from app.audio_analysis import _decode_mono
        from app.dub.engines import band_embedding

        rate = 16000
        audio = _decode_mono(src, rate)
        progress_cb(0.5, "Listening for speakers")
        return [band_embedding(audio[int(a * rate):int(b * rate)], rate) for a, b in spans]

    def speak_many(self, params: dict, texts: list[str], outs: list[Path], seed, progress_cb) -> list[float]:
        out = []
        for i, (text, path) in enumerate(zip(texts, outs)):
            progress_cb(i / max(1, len(texts)), f"Speaking line {i + 1}/{len(texts)}")
            res = self.generate_audio(text, {**params, "kind": "speech", "text": text, "duration_s": None},
                                      seed, path, lambda f, m: None)
            out.append(res.meta["duration_s"])
        return out

    def transcribe_many(self, paths: list[Path], language, progress_cb, expect: list[str] | None = None) -> list[str]:
        # the mock hears exactly what it was asked to say; tests patch this to hear mistakes
        progress_cb(0.5, "Checking the takes")
        return list(expect) if expect is not None else ["mock" for _ in paths]

    def generate_audio(self, prompt, params, seed, out_path: Path, progress_cb) -> DriverResult:
        # a tone (songs, music) or pink noise (sfx) at the exact length, so players and waveforms have
        # something real; the pitch follows prompt and seed so takes sound different
        self._tick(progress_cb, "Composing", upto=0.8)
        if params.get("kind") == "speech":
            seconds = max(0.5, self._speech_seconds(params))
        else:
            seconds = max(0.5, float(params.get("duration_s") or 10))
        if params.get("kind") == "sfx":
            src = f"anoisesrc=d={seconds:.3f}:c=pink:r=44100:a=0.25"
        else:
            h = zlib.crc32(f"{prompt}|{seed}".encode())
            freq, pulse = 180 + h % 500, 1 + (h >> 9) % 4
            wave = f"0.3*sin(2*PI*{freq}*t)*(0.6+0.4*sin(2*PI*{pulse}*t))"
            src = f"aevalsrc={wave}|{wave}:s=44100:d={seconds:.3f}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [get_settings().ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", src,
               "-t", f"{seconds:.3f}", "-ar", "44100", "-ac", "2", "-c:a", "flac", str(out_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='replace')[-400:]}") from e
        progress_cb(1.0, "Done")
        return DriverResult(out_path, "audio/flac", {"duration_s": round(seconds, 3), "model": params.get("model")})

    def generate_text(self, prompt, params, seed, out_path: Path, progress_cb) -> DriverResult:
        self._tick(progress_cb, "Writing")
        text = (
            f"[mock draft · seed {seed}]\n\n"
            f"{prompt.strip()}\n\n"
            "The camera lingers a moment longer than it should. Nobody speaks.\n"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        progress_cb(1.0, "Done")
        return DriverResult(out_path, "text/plain", {"chars": len(text)})
