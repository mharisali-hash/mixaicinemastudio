"""The presenter video's stage job and its cut.

The same orchestrator pattern as Poetry Short and the video podcast: it queues the GPU work as ordinary generate
jobs that point back at it (parent_job_id), defers itself while they run, and keeps its state on the
PresenterVideo row. Per scene, a part is made again only when its fingerprint changed.
"""
import copy
import logging
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app import audio_analysis as aa
from app import library as lib
from app import reel as rl
from app.autopilot import POLL_S, WAIT_PRIORITY, Skip, StageFailed, Wait
from app.config import get_settings
from app.models import Generation, Job, MediaItem, PresenterVideo, new_id, utcnow
from app.presenter import service as svc
from app.services import enqueue_generation, generation_file, new_seed, next_version

log = logging.getLogger("mixai.presenter")

SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024)}
TITLE_S = 3.0
LOWER_S = (0.6, 5.5)


def _w():
    from app import worker

    return worker


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Review:
    def __init__(self, message: str):
        self.message = message


class Run:
    def __init__(self, ctx):
        self.ctx, self.db, self.job = ctx, ctx.db, ctx.job
        p = self.job.payload or {}
        self.pv = self.db.get(PresenterVideo, p.get("presenter_id") or "")
        if self.pv is None or self.pv.workspace_id != self.job.workspace_id:
            raise svc.PresenterError("This presenter video no longer exists")
        self.mode = p.get("run") or "continue"
        self.quality = p.get("quality") if p.get("quality") in svc.QUALITIES else "draft"
        self.c = svc.character_of(self.db, self.pv)
        self.current: str | None = None
        self.r = copy.deepcopy(self.job.result or {})
        self.spins = 0

    def keys(self) -> tuple[str, ...]:
        return ("speech", "frames") if self.mode == "continue" else svc.STAGE_KEYS

    def rec(self) -> dict:
        return dict((self.pv.stages or {}).get(self.current) or {"status": "pending"})

    def put(self, rec: dict) -> None:
        self.pv.stages = {**(self.pv.stages or {}), self.current: rec}

    def put_scene(self, sc: dict) -> None:
        self.pv.scenes = [sc if s["id"] == sc["id"] else s for s in self.pv.scenes]
        flag_modified(self.pv, "scenes")

    def tick(self, frac: float = 0.0, message: str = "") -> None:
        keys = self.keys()
        i = keys.index(self.current) if self.current in keys else 0
        label = dict(svc.STAGES).get(self.current or "", "")
        self.r["stage"] = self.current
        self.job.result = copy.deepcopy(self.r)
        flag_modified(self.job, "result")
        self.ctx.progress((i + max(0.0, min(1.0, frac))) / len(keys), f"{label}: {message}" if message else label)

    def link(self, g: Generation) -> Generation:
        child = self.db.get(Job, g.job_id)
        child.payload = {**(child.payload or {}), "parent_job_id": self.job.id}
        return g

    def run(self) -> dict:
        for key in self.keys():
            self.current = key
            rec = self.rec()
            if rec.get("status") in ("done", "skipped"):
                continue
            reviewed = rec.get("status") == "review"
            rec.update(status="running", started_at=rec.get("started_at") or _iso(), detail=None)
            self.put(rec)
            self.tick()
            while True:
                out = getattr(self, f"stage_{key}")(reviewed)
                if isinstance(out, Wait):
                    self.wait(out)
                    continue
                break
            rec = self.rec()
            if isinstance(out, Review):
                rec.update(status="review", detail=out.message)
                self.put(rec)
                self.tick(1.0, out.message)
                return copy.deepcopy(self.r)
            rec.update(status="skipped" if isinstance(out, Skip) else "done", finished_at=_iso(),
                       detail=out.detail if isinstance(out, Skip) else None)
            self.put(rec)
            self.tick(1.0)
        self.r["waiting_on"] = []
        return copy.deepcopy(self.r)

    def pending(self, ids: list[str]) -> list[str]:
        return list(self.db.scalars(select(Job.id).where(Job.id.in_(ids), Job.status.in_(("queued", "running"))))) \
            if ids else []

    def wait(self, w: Wait) -> None:
        ids = list(dict.fromkeys(w.job_ids))
        self.r["waiting_on"] = ids
        self.tick(message=w.message)
        if not self.pending(ids):
            self.spins += 1
            if self.spins > 200:
                raise StageFailed("Stuck waiting for work that already finished; retry")
            return
        self.spins = 0
        lanes = getattr(self.ctx, "lanes", None)
        other = select(func.count()).select_from(Job).where(
            Job.status == "queued", Job.priority > WAIT_PRIORITY, Job.id != self.job.id,
            *([Job.lane.in_(lanes)] if lanes else []))
        if not self.db.scalar(other):
            deadline = time.monotonic() + POLL_S
            while time.monotonic() < deadline:
                time.sleep(1.0)
                self.tick(message=w.message)
                if not self.pending(ids):
                    return
                if self.db.scalar(other):
                    break
        self.db.commit()
        raise _w().JobDeferred(f"{dict(svc.STAGES)[self.current]}: {w.message}", priority=WAIT_PRIORITY)

    # one part of one scene: wait for it, use it, or (re)make it when its fingerprint changed. `path` is where
    # it lives on the scene: ("speech",), ("frame",) or ("take", "draft" | "final")
    def part(self, sc: dict, path: tuple[str, ...], fp: str, make, label: str) -> tuple[Generation | None, str | None]:
        holder = sc
        for k in path[:-1]:
            holder = holder.setdefault(k, {})
        cur = holder.get(path[-1]) or {}
        g = self.db.get(Generation, cur.get("gen") or "") if cur.get("fp") == fp else None
        if g is not None and g.status in svc.LIVE:
            return None, g.job_id
        if g is not None and g.status in svc.FINISHED and g.file_path:
            return g, None
        tries = cur.get("tries", 0) if cur.get("fp") == fp else 0
        if g is not None and g.status == "failed" and tries >= svc.ATTEMPTS:
            job = self.db.get(Job, g.job_id or "")
            raise StageFailed(f"Couldn't make {label} after {tries} tries: {(job.error if job else None) or 'no result'}")
        prompt, params = make()
        g = self.link(enqueue_generation(self.db, workspace_id=self.pv.workspace_id, project_id=None,
                                         target_type=svc.TARGET, target_id=sc["id"], kind=params.pop("_kind"),
                                         prompt=prompt, params=params))
        holder[path[-1]] = {"fp": fp, "gen": g.id, "tries": tries + 1}
        self.put_scene(sc)
        self.db.flush()
        return None, g.job_id

    def split_scene(self, sc: dict, dur: float) -> bool:
        """A voice slower than the splitter guessed: cut the scene by its measured length, aiming at 80% of the cap."""
        n = math.ceil(dur / (svc.MAX_SCENE_S * 0.8))
        # a little slack so the cut lands on a comma, not on one stray word
        parts = svc.split_text(sc["text"], max(20, math.ceil(len(sc["text"]) * 1.2 / n)))
        if len(parts) < 2 or len(self.pv.scenes) + len(parts) - 1 > svc.MAX_SCENES:
            return False
        fresh = [svc.new_scene(p, sc.get("background")) for p in parts]
        at = next(k for k, s in enumerate(self.pv.scenes) if s["id"] == sc["id"])
        self.pv.scenes = self.pv.scenes[:at] + fresh + self.pv.scenes[at + 1:]
        flag_modified(self.pv, "scenes")
        self.db.flush()
        return True

    def stage_speech(self, _reviewed: bool):
        waits, n = [], 0
        for i, sc in enumerate(self.pv.scenes, 1):
            def make(sc=sc):
                prompt, params = svc.speech_request(self.db, self.pv, sc, self.c)
                return prompt, {**params, "_kind": "speech"}

            g, w = self.part(sc, ("speech",), svc.speech_fp(self.pv, sc, self.c), make, f"scene {i}'s voice")
            if w:
                waits.append(w)
                continue
            dur = round(aa.stream_info(generation_file(g)).duration, 3)
            if dur > svc.MAX_SCENE_S:
                if self.split_scene(sc, dur):
                    # the pieces are new scenes with no voice yet: go round again and voice them
                    return self.stage_speech(_reviewed)
                raise StageFailed(f"Scene {i} is {dur:.0f} s spoken; a scene can be at most "
                                  f"{svc.MAX_SCENE_S:.0f} s. Split it into two")
            if sc.get("duration_s") != dur:
                sc["duration_s"] = dur
                self.put_scene(sc)
            n += 1
        if waits:
            return Wait(waits, f"{n} of {len(self.pv.scenes)} scenes voiced")
        return None

    def stage_frames(self, reviewed: bool):
        waits, n = [], 0
        for i, sc in enumerate(self.pv.scenes, 1):
            def make(sc=sc):
                prompt, params = svc.frame_request(self.db, self.pv, sc, self.c)
                return prompt, {**params, "_kind": "image"}

            g, w = self.part(sc, ("frame",), svc.frame_fp(self.db, self.pv, sc, self.c), make, f"scene {i}'s frame")
            if w:
                waits.append(w)
            else:
                n += 1
        if waits:
            return Wait(waits, f"{n} of {len(self.pv.scenes)} frames drawn")
        if self.mode == "continue" and not reviewed:
            return Review("Check the frames, then make the video")
        return None

    def stage_render(self, _reviewed: bool):
        waits, n, q = [], 0, self.quality
        for i, sc in enumerate(self.pv.scenes, 1):
            speech = self.db.get(Generation, (sc.get("speech") or {}).get("gen") or "")
            frame = self.db.get(Generation, (sc.get("frame") or {}).get("gen") or "")
            if speech is None or frame is None:
                raise StageFailed(f"Scene {i} has no voice or frame yet")

            def make(sc=sc, speech=speech, frame=frame):
                prompt, params = svc.take_request(self.db, self.pv, sc, self.c, q, speech, frame,
                                                  str(generation_file(speech)), float(sc["duration_s"] or 5))
                return prompt, {**params, "_kind": "video"}

            g, w = self.part(sc, ("take", q), svc.take_fp(sc, q, self.c), make, f"scene {i}'s video")
            if w:
                waits.append(w)
            else:
                n += 1
        if waits:
            return Wait(waits, f"{n} of {len(self.pv.scenes)} scenes filmed")
        return None

    def stage_assemble(self, _reviewed: bool):
        db, pv = self.db, self.pv
        rec = self.rec()
        run = rec.get("run") or {}
        if run.get("job") == self.job.id and run.get("assemble_job"):
            child = db.get(Job, run["assemble_job"])
            if child is not None and child.status in ("queued", "running"):
                return Wait([child.id], "Cutting the video")
            if child is not None and child.status == "done":
                res = child.result or {}
                pv.outputs = {**(pv.outputs or {}), self.quality: {
                    "media_id": run["media_id"], "generation_id": child.generation_id, "quality": self.quality,
                    "built_at": utcnow().isoformat() + "Z",
                    **{k: res.get(k) for k in ("duration_s", "width", "height", "captions", "fallback")}}}
                flag_modified(pv, "outputs")
                return None
            raise StageFailed(f"Cutting the video failed: {(child.error if child else None) or 'the job vanished'}")
        item, g = library_version(db, pv, self.quality)
        child = Job(workspace_id=pv.workspace_id, type=svc.ASSEMBLE_JOB, generation_id=g.id,
                    message="Waiting for a worker",
                    payload={"presenter_id": pv.id, "quality": self.quality, "parent_job_id": self.job.id})
        db.add(child)
        db.flush()
        g.job_id = child.id
        rec["run"] = {"job": self.job.id, "assemble_job": child.id, "media_id": item.id}
        self.put(rec)
        return Wait([child.id], "Cutting the video")


def handle(ctx) -> dict:
    W = _w()
    run = Run(ctx)
    try:
        return run.run()
    except (W.JobDeferred, W.WorkerStopping):
        raise
    except W.JobCancelled:
        run.db.rollback()
        run.db.commit()
        raise
    except Exception as e:
        log.exception("presenter %s failed in %s", run.pv.id, run.current)
        run.db.rollback()
        msg = str(e) or type(e).__name__
        pv = run.db.get(PresenterVideo, run.pv.id)
        if pv is not None and run.current:
            stages = dict(pv.stages or {})
            stages[run.current] = {**(stages.get(run.current) or {}), "status": "failed", "detail": msg[:500],
                                   "finished_at": _iso()}
            pv.stages = stages
        run.db.commit()
        label = dict(svc.STAGES).get(run.current or "", "Presenter video")
        raise RuntimeError(msg if msg.startswith(label) else f"{label} failed: {msg}") from e


# ---------------------------------------------------------------- the cut

def library_version(db, pv: PresenterVideo, quality: str) -> tuple[MediaItem, Generation]:
    w, h = SIZES[svc.settings_of(pv)["aspect"]]
    item = db.get(MediaItem, ((pv.outputs or {}).get(quality) or {}).get("media_id") or "") or \
        db.get(MediaItem, next((o.get("media_id") for o in (pv.outputs or {}).values() if o.get("media_id")), ""))
    if item is None or item.workspace_id != pv.workspace_id:
        item = MediaItem(id=new_id(), workspace_id=pv.workspace_id, kind="video", origin="generated",
                         title=(pv.title or "Presenter video")[:300], tags=["presenter"], width=w, height=h)
        db.add(item)
        db.flush()
    g = Generation(id=new_id(), workspace_id=pv.workspace_id, project_id=None, target_type="media",
                   target_id=item.id, kind="video", version=next_version(db, "media", item.id, "video"),
                   status="queued", prompt=pv.title or "Presenter video", seed=new_seed(),
                   parent_id=item.generation_id,
                   params={"presenter_id": pv.id, "quality": quality, "has_audio": True,
                           "aspect_ratio": svc.settings_of(pv)["aspect"],
                           "created_by": {"user_id": pv.created_by, "flow": "presenter"}})
    db.add(g)
    db.flush()
    if item.generation_id is None:
        item.generation_id = g.id
    lib.touch(item)
    return item, g


def _take(db, sc: dict, quality: str) -> Generation | None:
    for q in (quality, "draft"):
        g = db.get(Generation, ((sc.get("take") or {}).get(q) or {}).get("gen") or "")
        if g is not None and g.status in svc.FINISHED and g.file_path:
            return g
    return None


def handle_assemble(ctx) -> dict:
    """Each scene's take with its own voice under it, joined; captions, a name strip and a title card on top."""
    from app import overlays

    db, job = ctx.db, ctx.job
    p = job.payload or {}
    pv = db.get(PresenterVideo, p.get("presenter_id") or "")
    gen = db.get(Generation, job.generation_id or "")
    if pv is None or gen is None:
        raise svc.PresenterError("The presenter video or its Library record is gone")
    quality = p.get("quality") or "draft"
    st = svc.settings_of(pv)
    w, h = SIZES[st["aspect"]]
    gen.status = "generating"
    db.commit()
    work = get_settings().data_dir / "tmp" / "presenter" / job.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    folder, rel_folder = lib.media_dir(gen.workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{gen.id}.mp4"
    t0 = time.monotonic()
    try:
        parts, cues, at, fallback = [], [], 0.0, []
        scenes = pv.scenes or []
        for i, sc in enumerate(scenes):
            ctx.progress(0.05 + 0.6 * i / max(1, len(scenes)), f"Joining scene {i + 1} of {len(scenes)}")
            take = _take(db, sc, quality)
            speech = db.get(Generation, (sc.get("speech") or {}).get("gen") or "")
            if take is None or speech is None or generation_file(speech) is None:
                raise svc.PresenterError(f"Scene {i + 1} hasn't been filmed yet")
            if quality == "final" and (take.params or {}).get("quality") != "hq":
                fallback.append(i + 1)
            dur = float(sc.get("duration_s") or aa.stream_info(generation_file(speech)).duration)
            out = work / f"s{i:03d}.mp4"
            # the take's own sound (if any) is dropped: the voice take is the clean original
            rl.run_ff(["-i", str(generation_file(take)), "-i", str(generation_file(speech)), "-filter_complex",
                       f"[0:v]fps=25,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                       f"format=yuv420p,tpad=stop_mode=clone:stop_duration={dur:.3f}[v];"
                       f"[1:a]aresample=48000,apad=whole_dur={dur:.3f}[a]",
                       "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)])
            parts.append(rl.Entry(out))
            cues.append({"start": round(at, 3), "end": round(at + dur, 3), "text": sc["text"]})
            at += dur
        ctx.progress(0.7, "Joining the scenes")
        joined = work / "joined.mp4"
        lst = rl.write_concat_list(work, parts)
        rl.run_ff(rl.concat_reencode_cmd(lst, joined))
        ctx.progress(0.78, "Adding the captions")
        captioned, burned = work / "captioned.mp4", None
        if st.get("caption_style") == "none":
            shutil.copyfile(joined, captioned)
        else:
            burned = overlays.burn_captions(joined, captioned, cues, style=st.get("caption_style") or "classic",
                                            language=pv.language, width=w, height=h, timeout=7200)
        final = captioned
        cards = []
        if overlays.available():
            try:
                if st.get("title_card") and pv.title:
                    cards.append((overlays.card("title", width=w, height=h, duration=TITLE_S, language=pv.language,
                                                headline=pv.title), 0.0))
                if (st.get("lower_name") or "").strip():
                    a, b = LOWER_S
                    cards.append((overlays.card("lowerthird", width=w, height=h, duration=b - a,
                                                language=pv.language, name=st["lower_name"],
                                                role=st.get("lower_role") or ""), a + (TITLE_S if cards else 0.0)))
                if cards:
                    ctx.progress(0.9, "Adding the title and name")
                    final = work / "carded.mp4"
                    overlays.compose(captioned, cards, final)
            except Exception as e:  # noqa: BLE001 - a video without its cards beats no video
                log.warning("presenter cards failed: %s", e)
                final = captioned
        part = folder / f".{gen.id}.part.mp4"
        shutil.copyfile(final, part)
        part.replace(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.refresh(gen)
    rel = f"{rel_folder}/{dst.name}"
    dur = round(aa.stream_info(dst).duration, 3)
    gen.params = {**(gen.params or {}), "duration_s": dur, "width": w, "height": h}
    gen.file_path, gen.media_type, gen.status = rel, "video/mp4", "ready"
    return {"file_path": rel, "media_type": "video/mp4", "duration_s": dur, "width": w, "height": h,
            "captions": burned.get("style") if burned else None, "fallback": fallback,
            "seconds": round(time.monotonic() - t0, 3)}
