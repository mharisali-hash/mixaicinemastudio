"""The Short's cut (contract v33): `poetry_render`, ffmpeg on the general lane.

The story video's assembly (app.montage): each scene's clip fitted to its stretch of the song (trimmed,
slowed a little or held), cover-cropped, cross-faded; the song underneath; each line burned in as it is sung,
fading in, in Nastaliq with its aerab for Urdu; chapters per verse and chorus. An optional title card on the
first keyframe comes before the song. Output: a new version of the Short's Library video, plus the .srt and
a small preview in the Short's folder.
"""
import logging
import shutil
import time

from PIL import Image, ImageOps

from app import audio_analysis as aa
from app import library as lib
from app import montage as mt
from app import stories
from app.models import Generation, PoetryShort
from app.poetry import service as svc
from app.services import generation_file
from app.workdirs import job_workdir

log = logging.getLogger("mixai.poetry")

SECTION_NAMES = {"verse": "Verse", "chorus": "Chorus", "bridge": "Bridge", "intro": "Intro", "outro": "Outro",
                 "pre-chorus": "Pre-chorus", "hook": "Hook"}


def _open(path) -> Image.Image:
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def plan_for(ps: PoetryShort, duration: float, title_s: float, tail_s: float = 0.0) -> dict:
    """What montage.xfade_graph needs, plus the cues and chapters on the video's clock."""
    cues = [{"start": round(title_s + float(ln["start"]), 3), "end": round(title_s + float(ln["end"]), 3),
             "text": ln["text"]} for ln in ps.lines if ln.get("start") is not None and ln.get("end") is not None]
    marks, seen = [], {}
    for i, ln in enumerate(ps.lines):
        part = ln.get("part") or ln.get("section") or "verse"
        if marks and marks[-1][0] == part:
            continue
        sec = ln.get("section") or "verse"
        seen[sec] = seen.get(sec, 0) + 1
        name = SECTION_NAMES.get(sec, sec.replace("-", " ").capitalize())
        at = 0.0 if not marks else title_s + float(ln.get("start") or 0.0)
        marks.append((part, f"{name} {seen[sec]}" if sec != "chorus" else name, at))
    # the outro holds after the song; the audio is padded with silence to the end
    total = title_s + duration + tail_s
    chapters = [{"title": t, "start": round(a, 3), "end": round(marks[k + 1][2] if k + 1 < len(marks) else total, 3)}
                for k, (_, t, a) in enumerate(marks)]
    # the song fades out over its last moment instead of stopping dead on the last frame
    return {"audio_len": round(duration, 3), "title_s": title_s, "trimmed": True, "total": round(total, 3),
            "cues": cues, "chapters": chapters}


def _bookend(db, ps: PoetryShort, media_id: str | None, which: str):
    """(file, picture|video, seconds) for the intro or outro, or None. One deleted since it was picked is
    simply left out rather than failing the whole cut."""
    if not media_id:
        return None
    try:
        g, kind = svc.bookend_source(db, ps.workspace_id, media_id, which)
    except Exception:  # noqa: BLE001
        log.warning("%s %s of poetry %s is gone; cutting without it", which, media_id, ps.id)
        return None
    path = generation_file(g)
    if path is None or not path.is_file():
        return None
    if kind == "video":
        secs = min(svc.BOOKEND_MAX_S, max(1.0, aa.stream_info(path).duration or svc.BOOKEND_S))
    else:
        secs = svc.BOOKEND_S
    return path, kind, float(secs)


def handle_render(ctx) -> dict:
    db, job = ctx.db, ctx.job
    p = job.payload or {}
    gen = db.get(Generation, job.generation_id or "")
    ps = db.get(PoetryShort, p.get("poetry_id") or "")
    if gen is None or ps is None:
        raise svc.PoetryError("The Short or its Library record no longer exists")
    st = svc.settings_of(ps)
    aspect = st["aspect"]
    w, h = mt.SIZES[aspect]
    song = generation_file(db.get(Generation, (ps.song or {}).get("generation_id") or ""))
    if song is None or not song.is_file():
        raise svc.PoetryError("The song is gone; sing it again")
    if not svc.timed(ps) or not ps.scenes:
        raise svc.PoetryError("Time the lines and make the scenes first")
    gen.status = "generating"
    db.commit()

    duration = aa.stream_info(song).duration
    wins = svc.scene_windows(ps, duration)
    title_s = svc.TITLE_S if st.get("title_card") and ps.title else 0.0
    ends = {k: _bookend(db, ps, st.get(f"{k}_media_id"), k) for k in ("intro", "outro")}
    intro_s = ends["intro"][2] if ends["intro"] else 0.0
    outro_s = ends["outro"][2] if ends["outro"] else 0.0
    # the intro and the title card both come before the song, so they push it (and the captions) later
    plan = plan_for(ps, duration, intro_s + title_s, outro_s)
    parts = ([("intro", intro_s, ends["intro"])] if intro_s else []) + \
        ([("title", title_s, None)] if title_s else []) + \
        [("scene", b - a, sc) for (a, b), sc in zip(wins, ps.scenes)] + \
        ([("outro", outro_s, ends["outro"])] if outro_s else [])
    out_dir, _ = svc.files_dir(ps)
    out_dir.mkdir(parents=True, exist_ok=True)
    folder, rel_folder = lib.media_dir(gen.workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    work = job_workdir("poetry_render", job.id)
    dst = (folder / f"{gen.id}.mp4").resolve()
    srt, preview = out_dir / f"{gen.id}.srt", out_dir / f"{gen.id}_preview.mp4"
    held: list[int] = []
    t0 = time.monotonic()
    try:
        clips, durs = [], []
        for i, (kind, seconds, sc) in enumerate(parts):
            msg = f"Cutting part {i + 1} of {len(parts)}"
            ctx.progress(0.03 + 0.62 * i / len(parts), msg)
            tick = lambda i=i, msg=msg: ctx.progress(0.03 + 0.62 * i / len(parts), msg)  # noqa: E731
            frames = mt.slot_frames(seconds, i == len(parts) - 1)
            c = work / f"c{i:04d}.mp4"
            clips.append(c)
            durs.append(frames / mt.FPS)
            if kind in ("intro", "outro"):
                path, media_kind, _ = sc
                if media_kind == "video":
                    mt.video_clip(path, c, frames, aspect, tick=tick)
                else:
                    still = work / f"s{i:04d}.png"
                    stories.picture_frame(_open(path), aspect).save(still)
                    mt.clip(still, c, frames, aspect, zoom=False, tick=tick)
                    still.unlink(missing_ok=True)
                continue
            first = ps.scenes[0] if kind == "title" else sc
            kf = generation_file(db.get(Generation, first.get("keyframe") or "")) if first.get("keyframe") else None
            clip = generation_file(db.get(Generation, sc.get("clip") or "")) if sc and sc.get("clip") else None
            if kind == "scene" and clip is not None and clip.is_file():
                if mt.video_clip(clip, c, frames, aspect, tick=tick) == "held":
                    held.append(sc["n"])
                continue
            if kf is None or not kf.is_file():
                raise svc.PoetryError(f"Scene {first['n']} has no picture yet; make the scenes first")
            img = stories.title_frame(_open(kf), aspect, ps.title, ps.language) if kind == "title" else \
                stories.picture_frame(_open(kf), aspect)
            still = work / f"s{i:04d}.png"
            img.save(still)
            # a scene whose clip didn't come still moves: its keyframe with the story's slow zoom
            mt.clip(still, c, frames, aspect, tick=tick)
            still.unlink(missing_ok=True)
        ctx.progress(0.7, "Laying the song underneath")
        joined = work / "joined.mp4"
        mt.join(clips, durs, song, plan, joined, "16", tick=lambda: ctx.progress(0.75, "Laying the song underneath"))
        burned = mt.finish(joined, work, work / "short.mp4", aspect=aspect, language=ps.language, cues=plan["cues"],
                           title=ps.title, chapters=plan["chapters"], srt=srt, preview=preview, fade=svc.FADE_S,
                           progress=ctx.progress, caption_style=svc.settings_of(ps).get("caption_style") or "classic")
        (work / "short.mp4").replace(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.refresh(gen)
    rel = f"{rel_folder}/{dst.name}"
    dur = round(aa.stream_info(dst).duration, 3)
    gen.params = {**(gen.params or {}), "duration_s": dur, "width": w, "height": h}
    gen.file_path, gen.media_type, gen.status = rel, "video/mp4", "ready"
    out = {"file_path": rel, "media_type": "video/mp4", "duration_s": dur, "width": w, "height": h,
           "srt": srt.name, "preview": preview.name, "chapters": plan["chapters"], "cues": len(plan["cues"]),
           "held": held, "seconds": round(time.monotonic() - t0, 3)}
    if burned:
        out.update(renderer=burned.get("renderer"), fonts_missing=burned.get("fonts_missing") or [])
    return out
