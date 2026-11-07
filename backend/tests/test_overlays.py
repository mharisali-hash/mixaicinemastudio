"""Animated captions and titles (app.overlays): words, the CLI call, the ffmpeg graph and the classic fallback."""
import json
import subprocess
from pathlib import Path

import pytest

from app import overlays
from app.config import get_settings
from app.overlays import render as ov
from app.overlays.words import from_cues
from app.subtitles import Cue

URDU = [Cue(0.5, 2.5, "صبح کی ہوا میں پھول کھلے"), Cue(3.0, 4.0, "ندی کنارے")]


def test_words_share_the_cue_and_keep_its_order():
    words = from_cues(URDU)
    assert [w["text"] for w in words[:3]] == ["صبح", "کی", "ہوا"]
    assert words[0]["start"] == 0.5 and words[5]["end"] == pytest.approx(2.5, abs=0.01)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(words, words[1:]))
    assert {w["cue"] for w in words} == {0, 1}
    # a cue with WhisperX words keeps them
    heard = from_cues([{"start": 0, "end": 2, "text": "hello there",
                        "words": [{"word": "hello", "start": 0.1, "end": 0.6}, {"word": "there", "start": 0.9, "end": 1.4}]}])
    assert heard == [{"text": "hello", "start": 0.1, "end": 0.6, "cue": 0}, {"text": "there", "start": 0.9, "end": 1.4, "cue": 0}]


def test_caption_data_for_urdu_is_right_to_left_and_tall():
    d = ov.caption_data(URDU, "ur", "highlight")
    assert d["dir"] == "rtl" and d["tall"] is True and d["style"] == "highlight"
    # the words go in as written: no upper-casing anywhere
    assert d["words"][0]["text"] == "صبح"
    en = ov.caption_data([Cue(0, 1, "Salt on the window")], "en", "pill")
    assert en["dir"] == "ltr" and en["tall"] is False and en["colors"]["active"] == ov.PILL["active"]


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    """A HyperFrames that 'renders' by writing a file, so the call itself can be checked."""
    cli = tmp_path / "hf" / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fake")
    gsap = tmp_path / "hf" / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    gsap.parent.mkdir(parents=True)
    gsap.write_text("// gsap")
    monkeypatch.setattr(get_settings(), "hyperframes_dir", str(tmp_path / "hf"))
    monkeypatch.setattr(get_settings(), "overlays_engine", "auto")
    monkeypatch.setattr(ov.shutil, "which", lambda name: "/usr/bin/node")
    seen = {}
    real = subprocess.run

    def run(cmd, **kw):
        if cmd[0] != "/usr/bin/node":
            return real(cmd, **kw)
        seen["cmd"], seen["env"] = cmd, kw["env"]
        work = Path(cmd[cmd.index("render") + 1])
        seen["html"] = (work / "index.html").read_text(encoding="utf-8")
        seen["files"] = sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"webm")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ov.subprocess, "run", run)
    return seen


def test_render_runs_the_pinned_cli_quietly_and_caches(fake_cli):
    data = ov.caption_data(URDU, "ur", "clean")
    out = ov.render("captions.html", data, width=720, height=1280, duration=4.5, fps=25,
                    fonts={"caption.ttf": ov._font("ur", "صبح")})
    assert out.is_file() and out.suffix == ".webm"
    cmd, env = fake_cli["cmd"], fake_cli["env"]
    assert cmd[2] == "render" and cmd[cmd.index("--format") + 1] == "webm" and cmd[cmd.index("--fps") + 1] == "25"
    for key in ("HYPERFRAMES_NO_TELEMETRY", "HYPERFRAMES_NO_UPDATE_CHECK", "HYPERFRAMES_NO_AUTO_INSTALL"):
        assert env[key] == "1"
    html = fake_cli["html"]
    assert 'data-duration="4.5"' in html and 'data-width="720"' in html and "__" not in html.split("<script>")[0]
    blob = json.loads(html.split("var D = ")[1].split(";\n")[0])
    assert blob["dir"] == "rtl" and blob["words"][0]["text"] == "صبح"
    # everything it needs is local: no CDN, no Google Fonts
    assert fake_cli["files"] == ["fonts/caption.ttf", "gsap.min.js", "index.html"]
    assert "https://" not in html
    # the same overlay again comes from the cache
    fake_cli.clear()
    assert ov.render("captions.html", data, width=720, height=1280, duration=4.5, fps=25,
                     fonts={"caption.ttf": ov._font("ur", "صبح")}) == out
    assert fake_cli == {}


def test_user_words_cant_close_the_script(fake_cli):
    ov.render("captions.html", ov.caption_data([Cue(0, 1, "a </script><b>x")], "en", "pill"),
              width=640, height=360, duration=1)
    assert "</script><b>" not in fake_cli["html"].split("var D = ")[1].split(";\n")[0]


def test_compose_keeps_the_alpha_and_the_sound(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(ov.subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    ov.compose(tmp_path / "v.mp4", [(tmp_path / "a.webm", 0.0), (tmp_path / "b.webm", 2.5)], tmp_path / "o.mp4")
    cmd = seen["cmd"]
    # the vp9 decoder goes before each overlay, or the alpha is dropped
    a, b = cmd.index(str(tmp_path / "a.webm")), cmd.index(str(tmp_path / "b.webm"))
    assert cmd[a - 3:a] == ["-c:v", "libvpx-vp9", "-i"]
    assert cmd[b - 5:b - 3] == ["-itsoffset", "2.5"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v][1:v]overlay" in graph and "[v1][2:v]overlay" in graph and "eof_action=pass" in graph
    assert cmd[cmd.index("-map", cmd.index("[vout]")) + 1] == "0:a?"


def test_no_renderer_means_classic_captions(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "hyperframes_dir", str(tmp_path / "missing"))
    called = {}
    monkeypatch.setattr(ov.st, "burn", lambda *a, **kw: called.setdefault("kw", kw) or {"renderer": "ass",
                                                                                          "fonts_missing": []})
    res = overlays.burn_captions(tmp_path / "v.mp4", tmp_path / "o.mp4", URDU, style="pill", language="ur",
                                 width=720, height=1280)
    assert res["style"] == "classic" and "aren't installed" in res["fallback"] and called["kw"]["language"] == "ur"
    res = overlays.burn_captions(tmp_path / "v.mp4", tmp_path / "o.mp4", URDU, style="classic")
    assert res["style"] == "classic" and "fallback" not in res
    monkeypatch.setattr(get_settings(), "overlays_engine", "classic")
    assert ov.available() is False


def test_a_failed_render_still_gives_captions(fake_cli, tmp_path, monkeypatch):
    def broken(*a, **kw):
        raise ov.OverlayError("chrome crashed")

    monkeypatch.setattr(ov, "render", broken)
    monkeypatch.setattr("app.brand_render.video_info", lambda p: type("I", (), {"width": 720, "height": 1280,
                                                                               "duration": 4.0, "fps": 25.0})())
    monkeypatch.setattr(ov.st, "burn", lambda *a, **kw: {"renderer": "ass", "fonts_missing": []})
    res = overlays.burn_captions(tmp_path / "v.mp4", tmp_path / "o.mp4", URDU, style="highlight", language="ur")
    assert res["renderer"] == "ass" and res["style"] == "classic" and "failed" in res["fallback"]


def test_cards_carry_the_logo_and_the_words(fake_cli, tmp_path):
    logo = tmp_path / "logo.PNG"
    logo.write_bytes(b"png")
    ov.card("logo", width=1280, height=720, duration=2.5, name="Jafat", logo=logo)
    assert "logo.png" in fake_cli["files"] and '"kind": "logo"' in fake_cli["html"]
    ov.card("lowerthird", width=1280, height=720, duration=5, language="ur", name="عائشہ خان", role="میزبان")
    blob = json.loads(fake_cli["html"].split("var D = ")[1].split(";\n")[0])
    assert blob["dir"] == "rtl" and blob["name"] == "عائشہ خان" and sorted(fake_cli["files"])[:2] == [
        "fonts/body.ttf", "fonts/head.ttf"]


def test_poetry_and_podcast_take_a_caption_style(client):
    r = client.post("/api/poetry", json={"topic": "the moon", "caption_style": "karaoke"})
    assert r.status_code == 422
    r = client.post("/api/poetry", json={"topic": "the moon", "caption_style": "highlight"})
    assert r.status_code == 201, r.text
    assert r.json()["settings"]["caption_style"] == "highlight"
