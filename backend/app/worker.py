"""Job worker: `python -m app.worker [--lanes image,video,audio,general]` (cwd=backend).

One job at a time per process. Jobs are claimed with a conditional UPDATE so several
workers can share the table safely; on the two-GPU box there is one worker per lane
(image GPU, video GPU, general/LLM) so a still never waits behind a long take.
"""
import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

# `python -m app.worker` runs this file as __main__. Without this, the handlers' `from app import worker`
# loads a second copy, and the JobDeferred they raise isn't the class the loop below catches: a parent
# job waiting on its children (autopilot, dub, video podcast) was failed instead of re-queued
if __name__ == "__main__":
    sys.modules.setdefault("app.worker", sys.modules[__name__])

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.config import get_settings
from app.db import SessionLocal
from app.drivers import GenerationDriver, get_driver
from app.lanes import AUDIO_KINDS, IMAGE_KINDS, LANES, MODEL_KINDS, VIDEO_KINDS, gpu_lane, parse_lanes
from app.models import ACTING_USER, VIA_API, Generation, Job, UsageLedger, WorkerHeartbeat, utcnow

log = logging.getLogger("mixai.worker")

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(seconds=90)
HEARTBEAT_EVERY = 5.0
SWEEP_EVERY = 300.0
NEVER = datetime(1970, 1, 1)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

TEXT_KINDS = {"scene_text"}
EXT = {"image/png": ".png", "video/mp4": ".mp4", "text/plain": ".txt", "audio/flac": ".flac", "audio/mpeg": ".mp3",
       "audio/wav": ".wav", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "model/gltf-binary": ".glb"}
# same string as app.reel.ASSEMBLE_JOB (that module is imported at the bottom)
SERIAL_PER_PROJECT = ("reel_assemble",)

_stop = threading.Event()


class JobCancelled(Exception):
    pass


class WorkerStopping(Exception):
    pass


class JobDeferred(Exception):
    """A parent job handing itself back to the queue until the jobs it enqueued have run.

    One worker runs one job at a time, so a parent that blocked waiting for its children
    would starve them (see app.autopilot). Whatever the handler committed is kept."""

    def __init__(self, message: str = "Waiting", priority: int | None = None):
        super().__init__(message)
        self.message = message
        self.priority = priority


def _lane_filter(lanes) -> list:
    # all lanes (the classic single worker) needs no filter, which also keeps rows of an unknown lane runnable
    if not lanes or set(LANES) <= set(lanes):
        return []
    return [Job.lane.in_(tuple(lanes))]


def _fair_order() -> list:
    """Within a priority, the user with the fewest jobs running in the job's lane goes first, then whoever was
    served longest ago there, so one person's batch of twenty doesn't hold up everyone else.

    Priority stays first: a parent waiting on its children sits at WAIT_PRIORITY and must not jump ahead of
    other people's work just because its owner has nothing running (it would defer and come straight back)."""
    mine = aliased(Job)
    running = (select(func.count()).select_from(mine)
               .where(mine.user_id == Job.user_id, mine.lane == Job.lane, mine.status == "running")
               .correlate(Job).scalar_subquery())
    last = (select(func.max(mine.started_at))
            .where(mine.user_id == Job.user_id, mine.lane == Job.lane)
            .correlate(Job).scalar_subquery())
    # never served sorts first whatever the database does with NULLs
    return [Job.priority.desc(), running, func.coalesce(last, NEVER), Job.created_at]


def claim_next(db: Session, gpu: str | None = None, lanes=None) -> Job | None:
    # stitches of one project share scene mezzanines, so they run one after another
    other = aliased(Job)
    busy_project = exists().where(
        other.project_id == Job.project_id, other.type.in_(SERIAL_PER_PROJECT), other.status == "running",
    )
    for _ in range(5):
        job_id = db.scalars(
            select(Job.id)
            .where(Job.status == "queued", or_(Job.type.not_in(SERIAL_PER_PROJECT), ~busy_project),
                   *_lane_filter(lanes))
            .order_by(*_fair_order())
            .limit(1)
        ).first()
        if job_id is None:
            return None
        now = utcnow()
        res = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(
                status="running",
                started_at=now,
                heartbeat_at=now,
                updated_at=now,
                attempts=Job.attempts + 1,
                gpu=gpu,
                message="Starting",
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if res.rowcount == 1:
            return db.get(Job, job_id, populate_existing=True)
        # someone else won the race; try the next oldest
    return None


def recover_stuck(db: Session, stale_after: timedelta | None = None, lanes=None) -> int:
    """Put orphaned running jobs back in the queue (or fail them after MAX_ATTEMPTS)."""
    q = select(Job).where(Job.status == "running", *_lane_filter(lanes))
    if stale_after is not None:
        cutoff = utcnow() - stale_after
        q = q.where(or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < cutoff))
    n = 0
    for job in db.scalars(q).all():
        gen = db.get(Generation, job.generation_id) if job.generation_id else None
        if (job.payload or {}).get("inline_of"):
            # ran inside an autopilot job, which redoes the step itself when it resumes
            job.status = "failed"
            job.error = "Interrupted by a worker restart; the autopilot redoes this step"
            job.finished_at = utcnow()
            n += 1
            continue
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.error = f"Gave up after {job.attempts} attempts (worker kept dying mid-job)"
            job.finished_at = utcnow()
            if gen and gen.status in ("queued", "generating"):
                gen.status = "failed"
        else:
            job.status = "queued"
            job.progress = 0.0
            job.message = "Re-queued after worker restart"
            if gen and gen.status == "generating":
                gen.status = "queued"
        n += 1
    db.commit()
    return n


def beat(db: Session, lanes=None) -> None:
    hb = db.get(WorkerHeartbeat, WORKER_ID)
    if hb is None:
        s = get_settings()
        lanes = list(lanes or LANES)
        hb = WorkerHeartbeat(id=WORKER_ID, info={
            "driver": s.gen_driver, "pid": os.getpid(), "lanes": lanes,
            "comfy": {lane: s.comfy_urls_for(gpu_lane(lanes) if lane == "audio" else lane)
                      for lane in lanes if lane != "general"},
        })
        db.add(hb)
    hb.last_seen = utcnow()
    db.commit()


def heartbeat_lanes(hb: WorkerHeartbeat) -> list[str]:
    # rows from before lanes existed belong to an all-lanes worker
    return list((hb.info or {}).get("lanes") or LANES)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        return True  # os.kill(pid, 0) terminates the process on Windows; heartbeat age decides there
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def other_live_workers(db: Session, lanes=None) -> list[WorkerHeartbeat]:
    """Other live workers sharing one of these lanes. Same-host rows whose process is gone are pruned."""
    host = socket.gethostname()
    cutoff = utcnow() - STALE_AFTER
    out = []
    for hb in db.scalars(select(WorkerHeartbeat)).all():
        if hb.id == WORKER_ID:
            continue
        h, _, pid = hb.id.rpartition(":")
        if h == host and pid.isdigit() and not _pid_alive(int(pid)):
            db.delete(hb)  # crashed before it could clean up after itself
            continue
        if hb.last_seen >= cutoff and set(heartbeat_lanes(hb)) & set(lanes or LANES):
            out.append(hb)
    db.commit()
    return out


def recover_lanes(db: Session, lanes=None) -> int:
    """Called while this worker is idle. If nobody else serves our lanes, whatever still runs in them is
    an orphan; if another live worker does, it may be mid-job, so only take back stale ones."""
    stale_after = STALE_AFTER if other_live_workers(db, lanes) else None
    return recover_stuck(db, stale_after, lanes)


class JobContext:
    def __init__(self, db: Session, job: Job, driver_factory: Callable[[], GenerationDriver], lanes=None):
        self.db = db
        self.job = job
        self.driver_factory = driver_factory
        # the autopilot asks whether this worker has other queued work it could do instead
        self.lanes = tuple(lanes or LANES)
        self._last_beat = time.monotonic()

    def progress(self, fraction: float, message: str = "") -> None:
        db, job = self.db, self.job
        # status may have been flipped to cancelled by the API in another process
        current = db.scalar(select(Job.status).where(Job.id == job.id))
        if current == "cancelled":
            raise JobCancelled()
        if _stop.is_set():
            raise WorkerStopping()
        job.progress = max(0.0, min(1.0, fraction))
        if message:
            job.message = message[:500]
        job.heartbeat_at = utcnow()
        db.commit()
        # long GPU jobs would otherwise make /system/status report the worker as dead
        if time.monotonic() - self._last_beat >= HEARTBEAT_EVERY:
            beat(db)
            self._last_beat = time.monotonic()


def _out_path(gen: Generation, media_ext: str) -> tuple[Path, str]:
    if gen.target_type.startswith("implant_"):
        # outside every workspace folder: /api/media can't serve it, only /api/implant/files can
        rel = Path("clinic") / gen.workspace_id / "results" / f"{gen.id}{media_ext}"
        return get_settings().data_dir / rel, rel.as_posix()
    if gen.target_type == "media":
        rel = Path("workspaces") / gen.workspace_id / "media" / f"{gen.id}{media_ext}"
        return get_settings().data_dir / rel, rel.as_posix()
    project = gen.project_id or "_unassigned"
    rel = Path("workspaces") / gen.workspace_id / "projects" / project / "generations" / f"{gen.id}{media_ext}"
    return get_settings().data_dir / rel, rel.as_posix()


def _add_aerab(ctx: JobContext, gen: Generation) -> None:
    # without zer / zabar / pesh (or the tashkeel) the models guess the vowels and get many of them wrong
    from app import aerab
    from app.llm import LLMError

    p = dict(gen.params or {})

    def wanted(text: str) -> bool:
        return p.get("aerab", True) and aerab.applies(p.get("language"), text) and \
            any(map(aerab.unmarked, text.split("\n")))

    try:
        if gen.kind == "song":
            lyrics = p.get("lyrics") or ""
            if not wanted(lyrics):
                return
            ctx.progress(0.01, "Adding aerab to the lyrics")
            r = aerab.mark_sync(lyrics, aerab.target(p.get("language"), lyrics),
                                tick=lambda: ctx.progress(0.01, "Adding aerab to the lyrics"))
            if r.marked:
                gen.params = {**p, "lyrics": r.text, "lyrics_written": lyrics}
        elif gen.kind == "speech":
            text = p.get("text") or ""
            if not wanted(text):
                return
            ctx.progress(0.01, "Adding aerab to the script")
            r = aerab.mark_sync(text, aerab.target(p.get("language"), text),
                                tick=lambda: ctx.progress(0.01, "Adding aerab to the script"))
            if r.marked:
                from app import voices

                e = voices.ENGINES.get(p.get("engine") or "", voices.default())
                gen.params = {**p, "text": r.text, "text_written": text, "text_spoken": voices.spoken_text(r.text, e)}
        elif aerab.quoted_spoken(gen.prompt or ""):
            ctx.progress(0.01, "Adding aerab to the dialogue")
            marked = aerab.mark_quotes_sync(gen.prompt, tick=lambda: ctx.progress(0.01, "Adding aerab to the dialogue"))
            if marked != gen.prompt:
                gen.params = {**p, "prompt_written": gen.prompt}
                gen.prompt = marked
    except LLMError as e:
        # better an unmarked render than none
        log.warning("aerab skipped for %s: %s", gen.id, e)
        return
    ctx.db.commit()


def handle_generate(ctx: JobContext) -> dict:
    db, job = ctx.db, ctx.job
    gen = db.get(Generation, job.generation_id) if job.generation_id else None
    if gen is None:
        raise RuntimeError("Generation for this job no longer exists")
    from app.implant import guard as implant_guard

    if implant_guard.refuses(db, gen):
        raise RuntimeError("This uses a private Implant Preview picture, which can't be used anywhere else")

    if gen.target_type == "implant_result" and ((gen.params or {}).get("implant") or {}).get("engine") == "warp":
        # Implant Preview's shape warp: her own photo reshaped by measurement, no model and no GPU
        from app.implant.service import render_warp

        abs_path, rel_path = _out_path(gen, EXT["image/png"])
        ctx.progress(0.2, "Reshaping her photo")
        render_warp(db, gen, abs_path, tick=lambda: ctx.progress(0.4, "Finding the bust"))
        gen.file_path, gen.media_type, gen.status = rel_path, "image/png", "ready"
        return {"file_path": rel_path, "media_type": "image/png"}

    if gen.kind in IMAGE_KINDS:
        method, media_type = "generate_image", "image/png"
    elif gen.kind in VIDEO_KINDS:
        method, media_type = "generate_video", "video/mp4"
    elif gen.kind in AUDIO_KINDS:
        method, media_type = "generate_audio", "audio/flac"
    elif gen.kind in MODEL_KINDS:
        method, media_type = "generate_model", "model/gltf-binary"
    elif gen.kind in TEXT_KINDS:
        method, media_type = "generate_text", "text/plain"
    else:
        raise RuntimeError(f"No handler for generation kind '{gen.kind}'")

    if (gen.params or {}).get("compile_first"):
        from app.ai_jobs import fill_shot_prompt

        gen.prompt = fill_shot_prompt(ctx, gen)
        gen.params = {k: v for k, v in gen.params.items() if k != "compile_first"}
        db.commit()

    if ((gen.params or {}).get("magic_prompt") or {}).get("status") == "pending":
        from app.prompt_enhance import run_in_job

        run_in_job(ctx, gen)

    if (gen.params or {}).get("model") == "ideogram4":
        from app import ideogram_caption

        ideogram_caption.run_in_job(ctx, gen)

    if gen.kind in ("take", "video") and ((gen.params or {}).get("dialogue") or {}).get("status") == "pending":
        # character voices (contract v26): the lines are spoken and laid on a track first, or the take
        # goes back to the queue until they are
        from app import shot_speech

        shot_speech.run_in_job(ctx, gen)

    if gen.kind in MODEL_KINDS:
        # a text start waits for its picture (contract v40)
        from app import model3d

        model3d.run_in_job(ctx, gen)

    if get_settings().urdu_aerab and (gen.kind in VIDEO_KINDS or gen.kind in ("song", "speech")):
        _add_aerab(ctx, gen)

    # the prompt is final here; what Jafat won't picture is refused before a GPU is touched (app.content_policy)
    from app import content_policy

    # starts from the person's own negative, so the safety words add to it rather than replace it
    safety = {k: v for k, v in (gen.params or {}).items() if k in ("negative", "negative_prompt") and v}
    content_policy.check_before(ctx, gen, safety)
    from app import rights

    rights.check_render(ctx, gen)

    driver = ctx.driver_factory()
    job.gpu = job.gpu or getattr(driver, "name", None)
    gen.status = "generating"
    db.commit()

    if gen.kind in ("take", "video") and (gen.params or {}).get("longtake"):
        from app.longtake import run_take

        return run_take(ctx, gen, driver)

    abs_path, rel_path = _out_path(gen, EXT[media_type])
    params = {
        **(gen.params or {}),
        "kind": gen.kind,
        "version": gen.version,
        # the comfy driver resolves reference images / project aspect from these
        "generation_id": gen.id,
        "target_type": gen.target_type,
        "target_id": gen.target_id,
        "project_id": gen.project_id,
        **safety,
    }
    batch = image_batch.prepare(db, gen, params, _out_path) if gen.kind == "image" else []
    if batch:
        result, batch = image_batch.run(
            ctx, gen, batch, params, lambda p: getattr(driver, method)(gen.prompt, p, gen.seed, abs_path, ctx.progress))
    else:
        result = getattr(driver, method)(gen.prompt, params, gen.seed, abs_path, ctx.progress)
    if (gen.params or {}).get("ideogram_caption"):
        from app import ideogram_caption

        result = ideogram_caption.second_look(
            ctx, gen, lambda seed, path: getattr(driver, method)(gen.prompt, params, seed, path, ctx.progress),
            abs_path, result)
        if (gen.params or {}).get("exact_text"):
            from app import ideogram_text

            ctx.progress(0.99, "Setting the lettering")
            gen.params = {**gen.params, "exact_text_set": ideogram_text.set_text(abs_path, gen.params["exact_text"])}

    if getattr(result, "params_update", None):
        gen.params = {**(gen.params or {}), **result.params_update}
    if gen.kind == "song" and (gen.params or {}).get("sing_as"):
        # a child, a choir or a Library voice: the song is sung again in that voice (app.song_voice)
        from app import song_voice

        song_voice.apply(ctx, gen, abs_path, driver)
    if (gen.params or {}).get("mux_audio") and result.media_type.startswith("video/"):
        # a feature's take cut from the scene's dialogue track keeps that exact slice as its sound
        from app import film_audio

        film_audio.mux(abs_path, Path(gen.params["mux_audio"]))
    meta = dict(result.meta or {})
    if restore.needs_finish(gen.params):
        # Photo Studio: back to the source's full size, blended / composited (app.photo.restore)
        ctx.progress(0.995, "Finishing at full size")
        meta.update(restore.finish(gen, abs_path))
    if gen.target_type == "implant_result":
        from app.implant.service import finish_result

        ctx.progress(0.95, "Keeping everything but the chest as it was")
        finish_result(db, gen, abs_path,
                      lambda seed: getattr(driver, method)(gen.prompt, params, seed, abs_path, ctx.progress),
                      tick=lambda: ctx.progress(0.97, "Checking the result"))
    if batch:
        image_batch.finish(ctx, gen, batch, result)
    content_policy.check_after(ctx, gen, abs_path, result.media_type)
    gen.file_path = rel_path
    gen.media_type = result.media_type
    gen.status = "ready"
    return {"file_path": rel_path, "media_type": result.media_type, **meta}


HANDLERS: dict[str, Callable[[JobContext], dict]] = {
    "generate": handle_generate,
}

from app.ai_jobs import AI_HANDLERS  # noqa: E402  (LLM writing jobs live in their own module)

HANDLERS.update(AI_HANDLERS)
from app.longtake import handle_beats  # noqa: E402

HANDLERS["ai_beats"] = handle_beats

from app.reel import ASSEMBLE_JOB, handle_assemble  # noqa: E402

HANDLERS[ASSEMBLE_JOB] = handle_assemble
from app.upscale import JOB_TYPE as UPSCALE_JOB, handle_upscale  # noqa: E402

HANDLERS[UPSCALE_JOB] = handle_upscale
# called after a job reaches done/failed/cancelled in the worker; failures are logged, never fatal
FINISHED_HOOKS: list[Callable[[Session, Job], None]] = []

from app.autopilot import JOB_TYPE as AUTOPILOT_JOB, handle_autopilot, wake_parent  # noqa: E402

HANDLERS[AUTOPILOT_JOB] = handle_autopilot
FINISHED_HOOKS.append(wake_parent)
from app.library import on_job_finished as media_finished  # noqa: E402

FINISHED_HOOKS.append(media_finished)
from app import thumbs  # noqa: E402

FINISHED_HOOKS.append(thumbs.on_job_finished)
from app import image_batch  # noqa: E402

# Image → Create batches: the other pictures of the run are finished, or ended, with its job
FINISHED_HOOKS.append(image_batch.on_job_finished)
from app import brand  # noqa: E402

HANDLERS[brand.BRAND_JOB] = brand.handle_brand_apply
HANDLERS[brand.REVEAL_JOB] = brand.handle_logo_reveal
FINISHED_HOOKS.append(brand.on_job_finished)
from app import brand_templates  # noqa: E402

# a Brand template's scene hands over to its logo edit (contract v35)
FINISHED_HOOKS.append(brand_templates.on_job_finished)
from app import brand_moments  # noqa: E402

FINISHED_HOOKS.append(brand_moments.on_job_finished)
from app import organise  # noqa: E402

HANDLERS[organise.ZIP_JOB] = organise.handle_zip
from app.photo import looks as photo_looks, restore, service as photo  # noqa: E402

HANDLERS[photo.RENDER_JOB] = photo.handle_render
FINISHED_HOOKS.append(restore.on_job_finished)
HANDLERS[photo_looks.VIDEO_JOB] = photo_looks.handle_apply_video
from app import audio_analysis, audio_jobs  # noqa: E402

HANDLERS[audio_jobs.RENDER_JOB] = audio_jobs.handle_render
HANDLERS[audio_jobs.MIX_JOB] = audio_jobs.handle_mix
from app import voices  # noqa: E402

HANDLERS[voices.TRANSCRIBE_JOB] = voices.handle_transcribe
from app import voice_gallery  # noqa: E402

HANDLERS[voice_gallery.SAMPLE_JOB] = voice_gallery.handle_sample
FINISHED_HOOKS.append(audio_analysis.on_job_finished)
from app import dialogue  # noqa: E402

HANDLERS[dialogue.MIX_JOB] = dialogue.handle_mix
HANDLERS[dialogue.STEMS_JOB] = dialogue.handle_stems
FINISHED_HOOKS.append(dialogue.on_job_finished)
CPU_JOB_LEDGER_DIALOGUE = {dialogue.MIX_JOB: "audio_edit", dialogue.STEMS_JOB: "export"}
# CPU-only jobs: no GPU time billed, own ledger kind (wall time stays in job.result).
# The autopilot only orchestrates; its children bill their own GPU time.
CPU_JOB_LEDGER = {ASSEMBLE_JOB: "assembly", AUTOPILOT_JOB: "autopilot", brand.BRAND_JOB: "brand",
                  brand.REVEAL_JOB: "brand", organise.ZIP_JOB: "export", photo.RENDER_JOB: "photo",
                  photo_looks.VIDEO_JOB: "photo", audio_jobs.RENDER_JOB: "audio_edit", audio_jobs.MIX_JOB: "audio_edit"}
CPU_JOB_LEDGER.update(CPU_JOB_LEDGER_DIALOGUE)
from app import transcripts  # noqa: E402
from app.dub import project as dub_project, stages as dub_stages  # noqa: E402

HANDLERS[transcripts.TRANSCRIBE_JOB] = transcripts.handle_transcribe
HANDLERS[transcripts.CLEANUP_JOB] = transcripts.handle_cleanup
HANDLERS[transcripts.VIDEO_JOB] = transcripts.handle_video
HANDLERS[dub_project.STAGE_JOB] = dub_stages.handle_stage
HANDLERS[dub_project.EXPORT_JOB] = dub_stages.handle_export
CPU_JOB_LEDGER.update({transcripts.VIDEO_JOB: "export", dub_project.EXPORT_JOB: "export"})
from app.podcast import produce as podcast_produce, publish as podcast_publish, service as podcast, writer as podcast_writer  # noqa: E402

HANDLERS[podcast.WRITE_JOB] = podcast_writer.handle_write
HANDLERS[podcast.MIX_JOB] = podcast_produce.handle_mix
HANDLERS[podcast.PUBLISH_JOB] = podcast_publish.handle_publish
HANDLERS[podcast.VIDEO_JOB] = podcast_publish.handle_video
CPU_JOB_LEDGER.update({podcast.MIX_JOB: "audio_edit", podcast.PUBLISH_JOB: "export", podcast.VIDEO_JOB: "export"})
from app import stems  # noqa: E402

HANDLERS[stems.JOB] = stems.handle
FINISHED_HOOKS.append(stems.on_job_finished)
from app import remix  # noqa: E402

# effect versions and medleys are ffmpeg on the general lane; new beats, voices and mashups split on a GPU first
HANDLERS[remix.FX_JOB] = remix.handle_fx
HANDLERS[remix.JOB] = remix.handle
FINISHED_HOOKS.append(remix.on_job_finished)
CPU_JOB_LEDGER[remix.FX_JOB] = "audio_edit"
from app.podcast import video as video_podcast, video_stages as video_podcast_stages  # noqa: E402

# the orchestrator only waits on its takes (they bill their own GPU time); the cut is ffmpeg
HANDLERS[video_podcast.JOB] = video_podcast_stages.handle
HANDLERS[video_podcast.ASSEMBLE_JOB] = video_podcast_stages.handle_assemble
CPU_JOB_LEDGER.update({video_podcast.JOB: "autopilot", video_podcast.ASSEMBLE_JOB: "assembly"})
from app import character_sheet  # noqa: E402

# panels are ordinary image generations; the board is Pillow on the general lane
HANDLERS[character_sheet.BOARD_JOB] = character_sheet.handle_board
FINISHED_HOOKS.append(character_sheet.on_job_finished)
CPU_JOB_LEDGER[character_sheet.BOARD_JOB] = "export"
from app import stories  # noqa: E402

# the narration is GPU time (TTS, Whisper); the video is ffmpeg on the general lane
HANDLERS[stories.AUDIO_JOB] = stories.handle_audio
HANDLERS[stories.VIDEO_JOB] = stories.handle_video
from app import aerab  # noqa: E402

# POST /api/text/aerab/jobs: a whole book's marks, LLM work on the general lane
HANDLERS[aerab.JOB] = aerab.handle
CPU_JOB_LEDGER[stories.VIDEO_JOB] = "assembly"
from app.poetry import render as poetry_render, service as poetry, stages as poetry_stages, timing as poetry_timing  # noqa: E402

# Poetry Shorts: the orchestrator waits on its song, keyframes and clips; the timing listens on a GPU; the cut is ffmpeg
HANDLERS[poetry.JOB] = poetry_stages.handle
HANDLERS[poetry_timing.JOB] = poetry_timing.handle
HANDLERS[poetry.RENDER_JOB] = poetry_render.handle_render
CPU_JOB_LEDGER.update({poetry.JOB: "autopilot", poetry.RENDER_JOB: "assembly"})
from app.presenter import service as presenter, stages as presenter_stages  # noqa: E402

# Presenter Video: the orchestrator waits on its voice takes, frames and lip-synced takes; the cut is ffmpeg
HANDLERS[presenter.JOB] = presenter_stages.handle
HANDLERS[presenter.ASSEMBLE_JOB] = presenter_stages.handle_assemble
CPU_JOB_LEDGER.update({presenter.JOB: "autopilot", presenter.ASSEMBLE_JOB: "assembly"})
from app.ads import service as ads, stages as ad_stages  # noqa: E402

# Ad Studio: the orchestrator waits on its photos (they bill their own GPU time); the layout is Pillow
HANDLERS[ads.JOB] = ad_stages.handle
CPU_JOB_LEDGER[ads.JOB] = "autopilot"
from app.ads import style as ad_style  # noqa: E402

# reading the brand's own adverts is LLM work on the general lane
HANDLERS[ad_style.JOB] = ad_style.handle
CPU_JOB_LEDGER[ad_style.JOB] = "brand"
from app import model3d_logo  # noqa: E402

# a logo's badge is numpy and Pillow; only its cut-out (an ordinary generate job) uses the image GPU
HANDLERS[model3d_logo.JOB] = model3d_logo.handle
CPU_JOB_LEDGER[model3d_logo.JOB] = "model3d"
from app.bulk import export as bulk_export, run as bulk_run, service as bulk  # noqa: E402

# admin bulk runs: the orchestrator and the ZIP work are CPU; each picture is an ordinary generate job
HANDLERS[bulk.RUN_JOB] = bulk_run.handle
HANDLERS[bulk_run.UNZIP_JOB] = bulk_run.handle_unzip
HANDLERS[bulk.EXPORT_JOB] = bulk_export.handle
FINISHED_HOOKS.append(bulk.on_job_finished)
CPU_JOB_LEDGER.update({bulk.RUN_JOB: "autopilot", bulk_run.UNZIP_JOB: "export", bulk.EXPORT_JOB: "export"})
from app import edit_set  # noqa: E402

# Image Edit sets: the writer and the queueing are CPU; each picture is an ordinary generate job (wake_parent
# lifts the set when one finishes)
HANDLERS[edit_set.JOB] = edit_set.handle
CPU_JOB_LEDGER[edit_set.JOB] = "autopilot"


def _db_busy(e: BaseException) -> bool:
    # the lock also surfaces second-hand, as "transaction has been rolled back due to a previous exception"
    return "database is locked" in str(e)


def _close_generation(db: Session, job: Job, status: str = "failed") -> None:
    if job.generation_id:
        gen = db.get(Generation, job.generation_id)
        if gen and gen.status in ("queued", "generating"):
            gen.status = status


def run_job(db: Session, job: Job, driver_factory: Callable[[], GenerationDriver] | None = None,
            lanes=None) -> None:
    # the lane picks the ComfyUI instance: image GPU for stills, video GPU for takes and upscales.
    # Audio has no GPU of its own: both GPU workers claim it and render on whichever card they drive.
    lane = gpu_lane(lanes or LANES) if job.lane == "audio" else job.lane
    factory = driver_factory or (lambda: get_driver(get_settings().gen_driver, lane=lane))
    ctx = JobContext(db, job, factory, lanes)
    # an API job's results, child jobs and Library items are API rows too (contract v34)
    db.info[VIA_API] = bool(job.via_api)
    db.info[ACTING_USER] = job.user_id
    try:
        _run(db, job, ctx)
    finally:
        db.info.pop(VIA_API, None)
        db.info.pop(ACTING_USER, None)


def _run(db: Session, job: Job, ctx: JobContext) -> None:
    t0 = time.monotonic()
    handler = HANDLERS.get(job.type)
    try:
        if handler is None:
            raise RuntimeError(f"Unknown job type '{job.type}'")
        result = handler(ctx)
    except JobCancelled:
        db.rollback()
        db.refresh(job)
        job.message = "Cancelled"
        job.finished_at = utcnow()
        _close_generation(db, job)
        log.info("job %s cancelled", job.id)
    except JobDeferred as d:
        db.commit()
        db.refresh(job)
        if job.status == "cancelled":
            job.message = "Cancelled"
            job.finished_at = utcnow()
        else:
            job.status = "queued"
            # waiting isn't a failed attempt
            job.attempts = max(0, job.attempts - 1)
            job.message = d.message[:500]
            if d.priority is not None:
                job.priority = d.priority
    except WorkerStopping:
        db.rollback()
        db.refresh(job)
        # hand it back untouched; the attempt shouldn't count against it
        job.status = "queued"
        job.progress = 0.0
        job.attempts = max(0, job.attempts - 1)
        job.message = "Re-queued: worker shut down"
        _close_generation(db, job, status="queued")
        log.info("job %s re-queued on shutdown", job.id)
    except Exception as e:
        db.rollback()
        db.refresh(job)
        if _db_busy(e) and job.attempts < MAX_ATTEMPTS:
            # SQLite was locked past busy_timeout by another writer; the work itself didn't fail. A video podcast
            # four hours into its render was marked failed this way with all its takes made (24 Oct); resumable
            # jobs pick up where they stopped, the rest just run again
            job.status = "queued"
            job.message = "Re-queued: the database was busy"
            _close_generation(db, job, status="queued")
            log.warning("job %s re-queued after a database lock: %s", job.id, e)
            db.commit()
            return
        job.status = "failed"
        job.error = str(e) or type(e).__name__
        job.message = "Failed"
        job.finished_at = utcnow()
        _close_generation(db, job)
        log.exception("job %s failed", job.id)
    else:
        elapsed = round(time.monotonic() - t0, 3)
        # drivers that know the real GPU time (comfy history timestamps) report it
        if isinstance(result, dict) and result.get("gpu_seconds"):
            elapsed = float(result["gpu_seconds"])
        if job.type in CPU_JOB_LEDGER:
            elapsed = 0.0
        job.status = "done"
        job.progress = 1.0
        job.message = "Done"
        job.result = result
        job.finished_at = utcnow()
        job.gpu_seconds = elapsed
        db.add(UsageLedger(workspace_id=job.workspace_id, user_id=job.user_id, job_id=job.id, gpu_seconds=elapsed,
                           kind=CPU_JOB_LEDGER.get(job.type, job.type)))
    db.commit()
    if job.status in ("done", "failed", "cancelled"):
        for hook in FINISHED_HOOKS:
            try:
                hook(db, job)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("finished-hook %s failed for job %s", getattr(hook, "__name__", hook), job.id)


def process_one(
    session_factory: sessionmaker | Callable[[], Session] = SessionLocal,
    driver_factory: Callable[[], GenerationDriver] | None = None,
    lanes=None,
) -> bool:
    """Claim and run a single job. Returns False when the queue was empty."""
    db = session_factory()
    try:
        job = claim_next(db, gpu=get_settings().gen_driver, lanes=lanes)
        if job is None:
            return False
        log.info("running job %s (%s, %s lane)", job.id, job.type, job.lane)
        run_job(db, job, driver_factory, lanes)
        return True
    finally:
        db.close()


def sweep_subscriptions() -> None:
    # reads already treat a lapsed subscription as gone; this just makes the rows say so
    from app.packages import expire_due

    try:
        with SessionLocal() as db:
            n = expire_due(db)
            db.commit()
        if n:
            log.info("%d subscription(s) ended", n)
    except Exception:
        log.exception("subscription sweep failed")
    # Implant Preview patients past their retention period go, files and all
    from app.implant.service import purge_expired

    try:
        with SessionLocal() as db:
            n = purge_expired(db)
            db.commit()
        if n:
            log.info("%d implant patient file(s) past retention deleted", n)
    except Exception:
        log.exception("implant retention sweep failed")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.worker")
    ap.add_argument("--lanes", default="all",
                    help="comma list of image, video, audio, general (default: all, one worker does everything)")
    args = ap.parse_args(argv)
    try:
        lanes = parse_lanes(args.lanes)
    except ValueError as e:
        ap.error(str(e))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)

    def _on_signal(signum, _frame):
        if _stop.is_set():
            raise SystemExit(1)  # second Ctrl+C: stop waiting
        log.info("shutting down after the current step (Ctrl+C again to force)")
        _stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    # fail fast on a bad driver config instead of failing every job
    get_driver(s.gen_driver)

    with SessionLocal() as db:
        # only our lanes: the image worker must not grab the video worker's running take
        n = recover_lanes(db, lanes)
        if n:
            log.info("recovered %d job(s) left running by a previous worker", n)
        beat(db, lanes)

    log.info("worker %s up, lanes=%s, driver=%s, data=%s", WORKER_ID, ",".join(lanes), s.gen_driver, s.data_dir)
    if s.gen_driver == "comfy":
        for lane in lanes:
            if lane != "general":
                own = gpu_lane(lanes) if lane == "audio" else lane
                log.info("%s lane -> ComfyUI %s", lane, ", ".join(s.comfy_urls_for(own)) or "(none set)")
    last_beat = last_sweep = 0.0
    while not _stop.is_set():
        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_EVERY:
            with SessionLocal() as db:
                beat(db, lanes)
                recover_lanes(db, lanes)
            last_beat = now
        if now - last_sweep >= SWEEP_EVERY:
            sweep_subscriptions()
            last_sweep = now
        try:
            worked = process_one(lanes=lanes)
        except Exception:
            log.exception("worker loop error")
            worked = False
        if not worked:
            _stop.wait(1.0)

    with SessionLocal() as db:
        hb = db.get(WorkerHeartbeat, WORKER_ID)
        if hb:
            db.delete(hb)
            db.commit()
    log.info("worker stopped")


if __name__ == "__main__":
    main()
