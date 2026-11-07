"""The Poetry Short's stage machine (contract v33): poem -> song -> timing -> scenes -> render.

The same pattern as the AI video podcast (app.podcast.video_stages): the `poetry_short` job does the LLM work
inline, enqueues the GPU work (the song, the line timing, keyframes, clips) and the cut as jobs pointing back
at it (parent_job_id), and defers itself at WAIT_PRIORITY until wake_parent brings it back. Everything it
knows is on the PoetryShort row, so a retry, a restart or the next "continue" picks up where it stopped. With
settings.review on (the default) it stops after the poem, the song, the timing and the scenes for an OK.
"""
import copy
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app import aerab
from app import audio_analysis as aa
from app import library as lib
from app import song_voice
from app import models_catalog as mc
from app.autopilot import POLL_S, WAIT_PRIORITY, Skip, StageFailed, Wait
from app.llm import LLMError, chat_sync
from app.models import Generation, Job, MediaItem, PoetryShort, new_id, utcnow
from app.poetry import scenes as sn
from app.poetry import service as svc
from app.poetry import timing as tm
from app.services import enqueue_generation, generation_file

log = logging.getLogger("mixai.poetry")


def _w():
    from app import worker  # the worker imports this module at the bottom

    return worker


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Review:
    message: str


def sections_for(length_s: float) -> list[str]:
    # sung poetry is slow: a 60 s ghazal holds about eight lines, not twelve (twelve got cut off mid-poem)
    if length_s <= 75:
        return ["verse", "chorus"]
    if length_s <= 130:
        return ["verse", "chorus", "verse"]
    if length_s <= 190:
        return ["verse", "chorus", "verse", "chorus", "bridge", "chorus"]
    return ["verse", "chorus", "verse", "chorus", "verse", "bridge", "chorus"]


SLOW_STYLES = ("ghazal", "sufi", "nazm", "kalam", "qawwali", "naat", "slow", "ballad", "lullaby", "ney", "classical")
SONG_INTRO_S, SONG_OUTRO_S, LINE_GAP_S = 8.0, 6.0, 1.0
# ACE-Step's own limit; a 4-minute nasheed Episode is the longest
SONG_MAX_S = 240.0


def song_seconds(texts: list[str], style: str, asked: float, cap: float | None = None) -> float:
    """Long enough to sing every line. The song model stops at the length it is given, lyrics or not, so a
    poem longer than the song lost its last lines and the subtitles ran ahead of the singer."""
    per_letter = 0.4 if any(w in (style or "").lower() for w in SLOW_STYLES) else 0.28
    sung = sum(tm.weight(t) * per_letter + LINE_GAP_S for t in texts)
    need = SONG_INTRO_S + sung + SONG_OUTRO_S
    return float(round(min(cap or SONG_MAX_S, SONG_MAX_S, max(asked, need))))


def readable(g: Generation) -> bool:
    # a keyframe written while the server went down can be a truncated file that ComfyUI then can't load
    from PIL import Image

    p = generation_file(g)
    if p is None or not p.is_file():
        return False
    try:
        with Image.open(p) as im:
            im.load()
    except Exception:  # noqa: BLE001
        return False
    return True


class Run:
    def __init__(self, ctx):
        self.ctx, self.db, self.job = ctx, ctx.db, ctx.job
        p = self.job.payload or {}
        self.ps = self.db.get(PoetryShort, p.get("poetry_id") or "")
        if self.ps is None or self.ps.workspace_id != self.job.workspace_id:
            raise svc.PoetryError("This Poetry Short no longer exists")
        self.mode = p.get("run") if p.get("run") in ("continue", "render") else "continue"
        self.r = copy.deepcopy(self.job.result or {})
        self.current: str | None = None
        self.message = ""
        self.spins = 0

    @property
    def st(self) -> dict:
        return svc.settings_of(self.ps)

    # ---- state
    def keys(self) -> tuple[str, ...]:
        return svc.STAGE_KEYS if self.mode == "continue" else ("render",)

    def rec(self) -> dict:
        return svc.stage(self.ps, self.current)

    def put(self, rec: dict) -> None:
        svc.put_stage(self.ps, self.current, rec)

    def save(self) -> None:
        self.r["stage"] = self.current
        self.job.result = copy.deepcopy(self.r)
        flag_modified(self.job, "result")

    def tick(self, frac: float = 0.0, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        keys = self.keys()
        i = keys.index(self.current) if self.current in keys else 0
        label = dict(svc.STAGES).get(self.current or "", "")
        self.save()
        self.ctx.progress((i + max(0.0, min(1.0, frac))) / len(keys),
                          f"{label}: {self.message}" if label and self.message else self.message or label)

    def review(self, message: str) -> Review | None:
        return Review(message) if self.st.get("review", True) else None

    def link(self, g: Generation) -> Generation:
        child = self.db.get(Job, g.job_id)
        child.payload = {**(child.payload or {}), "parent_job_id": self.job.id}
        return g

    # ---- the loop
    def run(self) -> dict:
        for key in self.keys():
            self.current = key
            rec = self.rec()
            if rec.get("status") in ("done", "skipped"):
                continue
            reviewed = rec.get("status") == "review"
            self.ps.stage = key
            rec.update(status="running", started_at=rec.get("started_at") or _iso(), detail=None)
            self.put(rec)
            self.tick(0.0, "")
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
                self.r["waiting_on"] = []
                self.tick(1.0, out.message)
                return copy.deepcopy(self.r)
            rec.update(status="skipped" if isinstance(out, Skip) else "done", finished_at=_iso(),
                       detail=out.detail if isinstance(out, Skip) else rec.get("detail"))
            self.put(rec)
            self.tick(1.0, "")
        nxt = next((k for k in svc.STAGE_KEYS if not svc.done(self.ps, k)), None)
        self.ps.stage = nxt or "done"
        self.r["waiting_on"] = []
        self.save()
        return copy.deepcopy(self.r)

    def pending(self, ids: list[str]) -> list[str]:
        if not ids:
            return []
        return list(self.db.scalars(select(Job.id).where(Job.id.in_(ids), Job.status.in_(("queued", "running")))))

    def other_work_queued(self) -> bool:
        lanes = getattr(self.ctx, "lanes", None)
        lane_ok = [Job.lane.in_(lanes)] if lanes else []
        return bool(self.db.scalar(select(func.count()).select_from(Job).where(
            Job.status == "queued", Job.priority > WAIT_PRIORITY, Job.id != self.job.id, *lane_ok)))

    def wait(self, w: Wait) -> None:
        ids = list(dict.fromkeys(w.job_ids))
        self.r["waiting_on"] = ids
        self.tick(message=w.message)
        if not self.pending(ids):
            self.spins += 1
            if self.spins > 200:
                raise StageFailed("Stuck waiting for work that already finished; retry the job")
            return
        self.spins = 0
        if not self.other_work_queued():
            deadline = time.monotonic() + POLL_S
            while time.monotonic() < deadline:
                time.sleep(1.0)
                self.tick()
                if not self.pending(ids):
                    return
                if self.other_work_queued():
                    break
        self.db.commit()
        raise _w().JobDeferred(f"{dict(svc.STAGES)[self.current]}: {w.message}", priority=WAIT_PRIORITY)

    # ---------------------------------------------------------------- poem
    def stage_poem(self, reviewed: bool):
        ps = self.ps
        if not ps.lines:
            self.write_poem()
        if not ps.lines:
            raise StageFailed("The poem has no lines to sing")
        return None if reviewed else self.review("Read the poem; change any line you like, then continue")

    def write_poem(self) -> None:
        from app.api.audio import _Draft, _tidy, lyrics_messages  # the Song page's writer, prompt and all
        from app.schemas import LyricsIn

        ps, st = self.ps, self.st
        own = (ps.poem or {}).get("lyrics") if (ps.poem or {}).get("own") else ""
        tags = st.get("style") or ""
        if own:
            lyrics = own.strip()
        else:
            self.tick(0.1, "Writing the poem")
            body = LyricsIn(topic=(st.get("topic") or ps.title or "a poem")[:500], language=ps.language,
                            mood=st.get("mood") or None, style=st.get("style") or None,
                            sections=sections_for(float(st["length_s"])), aerab=True)
            try:
                res = chat_sync("creative", lyrics_messages(body), schema=_Draft, think=False, temperature=0.8,
                                max_tokens=1800, tick=lambda: self.tick(0.1))
            except LLMError as e:
                raise StageFailed(f"The lyrics writer is offline: {e}") from e
            lyrics = _tidy(res.data.lyrics)
            tags = " ".join(", ".join(x for x in (res.data.tags, tags) if x).split())[:300]
        written = lyrics
        if svc.wants_aerab(ps, lyrics):
            # writer or not, the marks get the strict second pass the lyrics route gives them
            self.tick(0.5, "Adding aerab")
            lyrics = aerab.finish_sync(lyrics, ps.language, tick=lambda: self.tick(0.5), what="poem").text
        ps.poem = {"own": bool(own), "lyrics": lyrics, "tags": tags, "written": written}
        ps.lines = svc.parse_lines(lyrics)
        if not ps.title and ps.lines:
            ps.title = lib.short_title(ps.lines[0]["text"], 6)[:200]

    # ---------------------------------------------------------------- song
    def make_song(self) -> Generation:
        ps, st = self.ps, self.st
        tags = (ps.poem or {}).get("tags") or ""
        sing = None
        if st["vocal"] in song_voice.VOCALS:
            sing = song_voice.plan(self.db, ps.workspace_id, st["vocal"], st.get("sing_voice_id"),
                                   (ps.poem or {}).get("lyrics") or "x")
        vocal = sing["base"] if sing else st["vocal"]
        prompt = ", ".join(x for x in (tags, st.get("mood"), f"{vocal} vocal") if x)
        m, chosen = mc.choose(svc.SONG_MODEL, "audio", prompt, kind="song")
        secs = song_seconds([ln["text"] for ln in ps.lines or []], " ".join((tags, st.get("style") or "")),
                            float(st["length_s"]), m.max_duration_s)
        mc.check_audio(m, "song", secs)
        params = {"kind": "song", "model": m.id, "duration_s": secs, "user_prompt": prompt,
                  "lyrics": (ps.poem or {}).get("lyrics") or "", "language": ps.language, "vocal": vocal,
                  "aerab": True, "poetry": ps.id, "created_by": {"user_id": ps.created_by, "flow": "poetry_short"},
                  **chosen}
        if sing:
            params["sing_as"] = sing
        item = self.db.get(MediaItem, (ps.song or {}).get("media_id") or "")
        if item is None:
            item = MediaItem(id=new_id(), workspace_id=ps.workspace_id, kind="audio", origin="generated",
                             title=f"{ps.title or 'Poetry Short'} · song"[:300], tags=["poetry"], duration_s=secs,
                             home="poetry")
            self.db.add(item)
            self.db.flush()
        g = enqueue_generation(self.db, workspace_id=ps.workspace_id, project_id=None, target_type="media",
                               target_id=item.id, kind="song", prompt=prompt, params=params,
                               parent_id=item.generation_id)
        item.generation_id = item.generation_id or g.id
        lib.touch(item)
        return g

    def stage_song(self, reviewed: bool):
        ps = self.ps
        rec = dict(ps.song or {})
        g = self.db.get(Generation, rec.get("generation_id") or "")
        if g is not None and g.status in svc.LIVE:
            return Wait([g.job_id], "Singing the poem")
        if g is not None and g.status in svc.FINISHED:
            if not rec.get("duration_s"):
                rec["duration_s"] = round(aa.stream_info(generation_file(g)).duration, 3)
                ps.song = rec
            return None if reviewed else self.review("Listen to the song; sing it again if you like, then continue")
        if g is not None and rec.get("tries", 0) >= svc.ATTEMPTS:
            job = self.db.get(Job, g.job_id or "")
            raise StageFailed(f"The song wouldn't come after {rec['tries']} tries: "
                              f"{(job.error if job else None) or 'no result'}")
        g = self.link(self.make_song())
        self.db.flush()
        rec.update(generation_id=g.id, media_id=g.target_id, tries=rec.get("tries", 0) + 1, duration_s=None)
        ps.song = rec
        return Wait([g.job_id], "Singing the poem")

    # ---------------------------------------------------------------- timing
    def apply_timing(self, res: dict, song_id: str, **extra) -> None:
        ps = self.ps
        rows = res.get("lines") or []
        if len(rows) != len(ps.lines):
            raise StageFailed("The timing came back for a different number of lines; time it again")
        ps.lines = [{**ln, "start": r["start"], "end": r["end"]} for ln, r in zip(ps.lines, rows)]
        ps.timing = {**(ps.timing or {}), "basis": song_id, "source": res.get("source"), "edited": False,
                     "phrases": res.get("phrases") or [], "separated": res.get("separated"), **extra}

    def stage_timing(self, reviewed: bool):
        ps = self.ps
        song_id = (ps.song or {}).get("generation_id")
        t = dict(ps.timing or {})
        if song_id and t.get("basis") == song_id and svc.timed(ps):
            return None if reviewed else self.review("Check when each line comes in, then continue")
        child = self.db.get(Job, t.get("job_id") or "")
        if child is not None and (child.payload or {}).get("song_generation_id") == song_id:
            if child.status in ("queued", "running"):
                return Wait([child.id], "Timing the lines to the singing")
            if child.status == "done":
                self.apply_timing(child.result or {}, song_id)
                return None if reviewed else self.review("Check when each line comes in, then continue")
            if t.get("tries", 0) >= svc.ATTEMPTS:
                # better lines spread over the song than a Short stuck here; the review is where they get fixed
                why = child.error or child.status
                self.apply_timing(tm.fit([ln["text"] for ln in ps.lines], [], svc.song_duration(ps) or 0.0),
                                  song_id, note=f"Couldn't listen to the song ({why}); the lines are spread evenly")
                rec = self.rec()
                rec["detail"] = ps.timing["note"][:500]
                self.put(rec)
                return None if reviewed else self.review(ps.timing["note"])
        child = Job(workspace_id=ps.workspace_id, type=tm.JOB, message="Waiting for a worker · Line timing",
                    payload={"poetry_id": ps.id, "song_generation_id": song_id, "language": ps.language,
                             "lines": [ln["text"] for ln in ps.lines], "whisper": bool(self.st.get("whisper", True)),
                             "parent_job_id": self.job.id})
        self.db.add(child)
        self.db.flush()
        same = (ps.timing or {}).get("tries_for") == song_id
        ps.timing = {**t, "job_id": child.id, "tries_for": song_id, "tries": (t.get("tries", 0) if same else 0) + 1}
        return Wait([child.id], "Timing the lines to the singing")

    # ---------------------------------------------------------------- scenes
    def stage_scenes(self, reviewed: bool):
        db, ps = self.db, self.ps
        if not ps.scenes:
            self.tick(0.05, "Picturing the poem")
            sn.plan(db, ps, tick=lambda: self.tick(0.05))
            db.flush()
        waits, failed, ready = [], [], 0
        n = len(ps.scenes)
        for k, sc in enumerate([dict(s) for s in ps.scenes]):
            kf = db.get(Generation, sc.get("keyframe") or "")
            if kf is not None and kf.status in svc.LIVE:
                waits.append(kf.job_id)
                continue
            broken = kf is not None and kf.status in svc.FINISHED and not readable(kf)
            if broken and not sc.get("remade_broken"):
                # once, whatever the tries: a file lost to a crash isn't the model's fault
                log.warning("poetry %s scene %s: keyframe %s won't open, making it again", ps.id, k + 1, kf.id)
                sc.update(keyframe_tries=min(sc.get("keyframe_tries", 0), svc.ATTEMPTS - 1), remade_broken=True)
            elif broken:
                failed.append((k, kf))
                continue
            if kf is None or kf.status not in svc.FINISHED or broken:
                if kf is not None and not broken and sc.get("keyframe_tries", 0) >= svc.ATTEMPTS:
                    failed.append((k, kf))
                    continue
                prompt, params = sn.keyframe_request(db, ps, sc)
                g = self.link(enqueue_generation(db, workspace_id=ps.workspace_id, project_id=None,
                                                 target_type="poetry_scene", target_id=sc["id"], kind="image",
                                                 prompt=prompt, params=params))
                sc.update(keyframe=g.id, keyframe_tries=sc.get("keyframe_tries", 0) + 1, clip=None, clip_tries=0,
                          clip_from=None, error=None)
                svc.put_scene(ps, sc)
                waits.append(g.job_id)
                continue
            clip = db.get(Generation, sc.get("clip") or "")
            fresh = clip is not None and sc.get("clip_from") == kf.id
            if fresh and clip.status in svc.LIVE:
                waits.append(clip.job_id)
                continue
            if fresh and clip.status in svc.FINISHED:
                ready += 1
                continue
            tries = sc.get("clip_tries", 0) if fresh else 0
            if fresh and tries >= svc.ATTEMPTS:
                failed.append((k, clip))
                continue
            prompt, params = sn.clip_request(db, ps, sc, k, kf)
            g = self.link(enqueue_generation(db, workspace_id=ps.workspace_id, project_id=None,
                                             target_type="poetry_scene", target_id=sc["id"], kind="video",
                                             prompt=prompt, params=params))
            sc.update(clip=g.id, clip_from=kf.id, clip_tries=tries + 1, error=None)
            svc.put_scene(ps, sc)
            waits.append(g.job_id)
        if waits:
            return Wait(waits, f"{ready} of {n} scenes ready")
        if failed:
            for k, g in failed:
                job = db.get(Job, g.job_id or "")
                sc = {**ps.scenes[k], "error": ((job.error if job else None) or "no result")[:500]}
                svc.put_scene(ps, sc)
            # handle() rolls back on the way out; the per-scene reasons should survive it
            db.commit()
            k, g = failed[0]
            raise StageFailed(f"Scene{'s' if len(failed) > 1 else ''} {', '.join(str(k + 1) for k, _ in failed)} "
                              f"wouldn't render: {ps.scenes[k]['error']}")
        return None if reviewed else self.review("Look over the scenes; make any of them again, then continue")

    # ---------------------------------------------------------------- render
    def stage_render(self, _reviewed: bool):
        db, ps = self.db, self.ps
        rec = self.rec()
        run = rec.get("run") or {}
        if run.get("job") == self.job.id and run.get("render_job"):
            child = db.get(Job, run["render_job"])
            if child is not None and child.status in ("queued", "running"):
                return Wait([child.id], "Cutting the Short")
            if child is not None and child.status == "done":
                res = child.result or {}
                ps.outputs = {"media_id": run["media_id"], "generation_id": child.generation_id,
                              "built_at": utcnow().isoformat() + "Z",
                              **{k: res.get(k) for k in ("duration_s", "width", "height", "srt", "preview",
                                                         "chapters", "renderer", "fonts_missing")}}
                return None
            raise StageFailed(f"Cutting the video failed: {(child.error if child else None) or 'the job disappeared'}")
        item, g = svc.library_version(db, ps)
        child = Job(workspace_id=ps.workspace_id, type=svc.RENDER_JOB, generation_id=g.id, message="Waiting for a worker",
                    payload={"poetry_id": ps.id, "media_id": item.id, "parent_job_id": self.job.id})
        db.add(child)
        db.flush()
        g.job_id = child.id
        rec["run"] = {"job": self.job.id, "render_job": child.id, "media_id": item.id}
        self.put(rec)
        return Wait([child.id], "Cutting the Short")


def handle(ctx) -> dict:
    W = _w()
    run = Run(ctx)
    try:
        return run.run()
    except (W.JobDeferred, W.WorkerStopping):
        raise
    except W.JobCancelled:
        run.db.rollback()
        ps = run.db.get(PoetryShort, run.ps.id)
        if ps is not None and run.current:
            rec = svc.stage(ps, run.current)
            if rec.get("status") == "running":
                rec.update(status="pending", detail="Cancelled")
                svc.put_stage(ps, run.current, rec)
        run.db.commit()
        raise
    except Exception as e:
        log.exception("poetry short %s failed in %s", run.ps.id, run.current)
        run.db.rollback()
        msg = str(e) or type(e).__name__
        if isinstance(e, LLMError):
            msg = f"The AI couldn't be reached: {msg}"
        ps = run.db.get(PoetryShort, run.ps.id)
        if ps is not None and run.current:
            rec = svc.stage(ps, run.current)
            rec.update(status="failed", detail=msg[:500], finished_at=_iso())
            svc.put_stage(ps, run.current, rec)
        job = run.db.get(Job, run.job.id)
        job.result = {**(job.result or {}), "waiting_on": []}
        run.db.commit()
        label = dict(svc.STAGES).get(run.current or "", "Poetry Short")
        raise RuntimeError(msg if msg.startswith(label) else f"{label} failed: {msg}") from e
