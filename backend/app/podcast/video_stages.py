"""The AI video podcast's stage machine (contract v24) and its assembly job.

Same pattern as the Quick Create autopilot (app.autopilot): one worker runs one job at a time, so the
`video_podcast` job never sits waiting for its portraits and takes. It does what it can, enqueues the GPU
work as ordinary generate jobs pointing back at it (parent_job_id), and defers itself at WAIT_PRIORITY until
wake_parent brings it back. Its state lives on the VideoPodcast row, so a retry, a restart or the next
"continue" picks up where it stopped. It also stops on purpose at the two review points.
"""
import copy
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app import audio_analysis as aa
from app import brand_render as br
from app import library as lib
from app import models_catalog
from app import reel as rl
from app import storyboard as sb
from app import transcripts as tr
from app.autopilot import POLL_S, WAIT_PRIORITY, Skip, StageFailed, Wait
from app.brand_schemas import CardSettings
from app.drivers.comfy import HQ_SIZES, VIDEO_SIZES
from app.llm import LLMError, chat_sync
from app.models import Character, Generation, Job, MediaItem, PodcastShow, Project, VideoPodcast, utcnow
from app.podcast import video as vd
from app.podcast import vplan
from app.services import approve, enqueue_generation, generation_file
from app.workdirs import job_workdir

log = logging.getLogger("mixai.video_podcast")

ATTEMPTS = 2  # first try plus one re-roll, for portraits, frames and takes alike
LANG3 = {"en": "eng", "ur": "urd", "ar": "ara", "hi": "hin", "fa": "fas", "ps": "pus", "sd": "snd", "pa": "pan",
         "pnb": "pnb", "bn": "ben", "tr": "tur", "fr": "fra", "de": "deu", "es": "spa"}


def _w():
    from app import worker  # the worker imports this module at the bottom

    return worker


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Review:
    message: str


class HostLook(BaseModel):
    description: str = Field(min_length=1)


def look_messages(name: str, role: str, persona: str, language: str) -> list[dict]:
    return [
        {"role": "system", "content": "You are a casting director for a filmed podcast. You describe how a host "
                                      "looks so a photographer can shoot their portrait."},
        {"role": "user", "content": (
            f"The host is {name}, the show's {role}. Persona: {persona or 'not given'}. The show is in "
            f"language '{language}'.\nIn 40 to 70 words describe only what they look like: age range, face, hair, "
            "skin tone, build and clothes a podcast host would wear. No name, no personality, no setting, no text "
            "on clothing. Answer as JSON {\"description\": \"...\"}.")},
    ]


def portrait_prompt(desc: str) -> str:
    return (f"Photorealistic head-and-shoulders portrait of {desc}, a podcast host, slight three-quarter turn, "
            "relaxed friendly expression, plain neutral grey background, soft even studio light, sharp focus on the "
            "eyes, natural skin texture.")


PHOTO_PROMPT = ("Picture 1 is a photo of a real person. Make a clean photorealistic head-and-shoulders reference "
                "portrait of exactly this person: keep the face, features, skin tone, hair and age exactly the same, "
                "relaxed expression, plain neutral grey background, soft even studio light, sharp focus.")


class Run:
    def __init__(self, ctx):
        self.ctx, self.db, self.job = ctx, ctx.db, ctx.job
        p = self.job.payload or {}
        self.vp = self.db.get(VideoPodcast, p.get("video_podcast_id") or "")
        if self.vp is None or self.vp.workspace_id != self.job.workspace_id:
            raise vd.VideoPodcastError("This AI video no longer exists")
        self.ep = vd.episode_of(self.db, self.vp)
        self.project = self.db.get(Project, self.vp.project_id)
        self.mode = p.get("run") or "continue"
        self.quality = p.get("quality") if p.get("quality") in vd.QUALITIES else "draft"
        self.track_ids = list(p.get("track_ids") or []) or None
        self.from_piece = int(p["from_piece"]) if p.get("from_piece") else None
        vd.upgrade(self.db, self.vp, self.ep)
        self.r = copy.deepcopy(self.job.result or {})
        self.current: str | None = None
        self.message = ""
        self.spins = 0

    # ---- state
    def keys(self) -> tuple[str, ...]:
        return {"continue": vd.CONTINUE_STAGES, "render": ("render", "assemble"),
                "assemble": ("assemble",)}[self.mode]

    def st(self) -> dict:
        return vd.stage(self.vp, self.current)

    def put(self, rec: dict) -> None:
        vd.put_stage(self.vp, self.current, rec)

    def save(self) -> None:
        self.r["stage"] = self.current
        self.job.result = copy.deepcopy(self.r)
        flag_modified(self.job, "result")

    def tick(self, frac: float = 0.0, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        keys = self.keys()
        i = keys.index(self.current) if self.current in keys else 0
        label = dict(vd.STAGES).get(self.current or "", "")
        self.save()
        self.ctx.progress((i + max(0.0, min(1.0, frac))) / len(keys),
                          f"{label}: {self.message}" if label and self.message else self.message or label)

    def link(self, g: Generation) -> Generation:
        g.params = {**(g.params or {}), "vp": self.vp.id}
        child = self.db.get(Job, g.job_id)
        child.payload = {**(child.payload or {}), "parent_job_id": self.job.id}
        return g

    # ---- the loop
    def run(self) -> dict:
        for key in self.keys():
            self.current = key
            rec = self.st()
            if rec.get("status") in ("done", "skipped"):
                continue
            reviewed = rec.get("status") == "review"
            self.vp.stage = key
            rec.update(status="running", started_at=rec.get("started_at") or _iso(), detail=None)
            self.put(rec)
            self.tick(0.0, "")
            while True:
                out = getattr(self, f"stage_{key}")(reviewed)
                if isinstance(out, Wait):
                    self.wait(out)
                    continue
                break
            rec = self.st()
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
        nxt = next((k for k, _ in vd.STAGES if vd.stage(self.vp, k).get("status") not in ("done", "skipped")), None)
        self.vp.stage = nxt or "done"
        self.r.update(waiting_on=[], eta_s=0)
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
        raise _w().JobDeferred(f"{dict(vd.STAGES)[self.current]}: {w.message}", priority=WAIT_PRIORITY)

    # ---- one slot: wait for what's live, use what's done, else make (or re-make) it
    def slot(self, gens: list[Generation], make, label: str) -> tuple[Generation | None, list[str]]:
        live = [g.job_id for g in gens if g.status in vd.LIVE and g.job_id]
        if live:
            return None, live
        done = next((g for g in gens if g.status == "approved"), None) or \
            next((g for g in gens if g.status == "ready"), None)
        if done is not None:
            return done, []
        tries = [g for g in gens if g.status == "failed"]
        if len(tries) >= ATTEMPTS:
            job = self.db.get(Job, tries[0].job_id or "")
            raise StageFailed(f"Couldn't make {label} after {len(tries)} tries: {(job.error if job else None) or 'no result'}")
        g = self.link(make())
        self.db.flush()
        return None, [g.job_id]

    # ---------------------------------------------------------------- stages
    def stage_cast(self, reviewed: bool):
        db, vp = self.db, self.vp
        vd.sync_hosts(db, vp, self.ep)
        hosts = vd.hosts_in_order(vp, self.ep)
        waits: list[str] = []
        base: dict[str, Generation] = {}
        for n, h in enumerate(hosts):
            entry = dict(vp.hosts[h["id"]])
            c = db.get(Character, entry["character_id"])
            if entry.get("source", "persona") == "persona" and not c.description.strip():
                c.description = self.appearance(h)
            gens = vd.fresh(vd.portraits(db, c.id), "vp_rev", entry.get("rev"))

            def make(c=c, entry=entry):
                params = {"vp_rev": entry.get("rev")}
                if entry.get("source") == "photo":
                    params["reference_ids"] = [entry["photo_generation_id"]]
                    prompt = PHOTO_PROMPT
                else:
                    models_catalog.apply_image_choice(params, self.project.settings)
                    prompt = portrait_prompt(c.description or c.name)
                return enqueue_generation(db, workspace_id=vp.workspace_id, project_id=vp.project_id,
                                          target_type="character", target_id=c.id, kind="portrait", prompt=prompt,
                                          params=params)

            g, w = self.slot(gens, make, f"{h['name']}'s portrait")
            waits += w
            if g is not None:
                base[h["id"]] = g
            self.tick(0.5 * (n + 1) / max(1, len(hosts)))
        if waits:
            return Wait(waits, f"{len(base)} of {len(hosts)} portraits ready")
        if reviewed:
            for g in base.values():
                if g.status == "ready":
                    approve(db, g)
                    g.params = {**(g.params or {}), "approved_by": "video_podcast"}
        for h in hosts:
            waits += self.sheets(h, base[h["id"]])
        if waits:
            total = 2 * len(hosts)
            # these run on the image GPU one by one; a count shows the wait is moving
            return Wait(waits, f"Turning the hosts for the side angles: {total - len(waits)} of {total} views ready")
        return None if reviewed else Review("Check your hosts, then continue")

    def appearance(self, h: dict) -> str:
        self.tick(message=f"Picturing {h['name']}")
        try:
            res = chat_sync("creative", look_messages(h["name"], h.get("role") or "host", h.get("persona") or "",
                                                      self.ep.language),
                            schema=HostLook, temperature=0.6, max_tokens=600, tick=lambda: self.tick())
            return " ".join(res.data.description.split())[:600]
        except LLMError as e:
            log.warning("host appearance fell back to the persona: %s", e)
            return f"{h['name']}, {h.get('persona') or 'a friendly podcast host'}"[:600]

    def sheets(self, h: dict, portrait: Generation) -> list[str]:
        """Front and 3/4 sheet views made from this portrait; a view that won't render is left out."""
        db, vp = self.db, self.vp
        entry = dict(vp.hosts[h["id"]])
        sheets = dict(entry.get("sheets") or {})
        if sheets.get("from") != portrait.id:
            sheets = {"from": portrait.id}
        waits = []
        for view in ("front", "three_quarter"):
            g = db.get(Generation, sheets.get(view) or "")
            if g is not None and g.status in vd.LIVE:
                waits.append(g.job_id)
                continue
            if g is not None and (g.status in vd.FINISHED or sheets.get(f"{view}_tries", 0) >= ATTEMPTS):
                continue
            g = self.link(enqueue_generation(
                db, workspace_id=vp.workspace_id, project_id=vp.project_id, target_type="character",
                target_id=entry["character_id"], kind="sheet_view", prompt="",
                params={"view": view, "reference_ids": [portrait.id], "from_portrait": portrait.id}))
            db.flush()
            sheets[view] = g.id
            sheets[f"{view}_tries"] = sheets.get(f"{view}_tries", 0) + 1
            waits.append(g.job_id)
        entry["sheets"] = sheets
        vd.put_host(vp, h["id"], entry)
        return waits

    def stage_set(self, _reviewed: bool):
        db, vp = self.db, self.vp
        loc = vd.ensure_location(db, vp)
        n = len(vd.hosts_in_order(vp, self.ep))
        key = vd.set_key(vp, n)
        gens = vd.fresh(list(db.scalars(select(Generation).where(
            Generation.target_type == "location", Generation.target_id == loc.id, Generation.kind == "establishing",
        ).order_by(Generation.created_at.desc()))), "vp_set", key)

        def make():
            prompt, params = sb.prepare_location_generation(db, loc, "establishing", vd.set_prompt(vp, n),
                                                            {"aspect_ratio": vd.settings_of(vp)["aspect"],
                                                             "vp_set": key})
            return enqueue_generation(db, workspace_id=vp.workspace_id, project_id=vp.project_id,
                                      target_type="location", target_id=loc.id, kind="establishing",
                                      prompt=prompt, params=params)

        g, waits = self.slot(gens, make, "the set")
        if waits:
            return Wait(waits, "Building the set")
        if g.status == "ready":
            approve(db, g)
        return None

    def stage_keyframes(self, reviewed: bool):
        db, vp = self.db, self.vp
        self.project.aspect_ratio = vd.settings_of(vp)["aspect"]
        shots = vd.ensure_angle_shots(db, vp, self.ep)
        waits: list[str] = []
        ready: list[Generation] = []
        for key, host_id, angle in vd.angle_keys(vp, self.ep):
            shot = shots[key]
            ids, labels = vd.angle_refs(db, vp, self.ep, host_id, angle)
            tag = vd.angle_tag(ids, shot.start_prompt)
            gens = vd.fresh(list(db.scalars(select(Generation).where(
                Generation.target_type == "shot", Generation.target_id == shot.id, Generation.kind == "keyframe_start",
            ).order_by(Generation.created_at.desc()))), "vp_refs", tag)

            def make(shot=shot, ids=ids, labels=labels, tag=tag):
                params = {"aspect_ratio": vd.settings_of(vp)["aspect"], "vp_refs": tag}
                if ids:
                    params.update(reference_ids=ids, reference_labels=labels)
                prompt, params = sb.prepare_shot_generation(db, shot, "keyframe_start", "", params)
                return enqueue_generation(db, workspace_id=vp.workspace_id, project_id=vp.project_id,
                                          target_type="shot", target_id=shot.id, kind="keyframe_start",
                                          prompt=prompt, params=params)

            g, w = self.slot(gens, make, f"the {vd.ANGLE_LABELS[angle].lower()} frame")
            waits += w
            if g is not None:
                ready.append(g)
        if waits:
            return Wait(waits, f"{len(ready)} of {len(shots)} camera angles ready")
        if not reviewed:
            return Review("Check the shots' look, then continue")
        for g in ready:
            if g.status == "ready":
                approve(db, g)
        for s in shots.values():
            s.stale = False
        return None

    def stage_plan(self, _reviewed: bool):
        db, vp = self.db, self.vp
        mix = vd.mix_of(self.ep)
        if mix is None:
            raise StageFailed("Mix the episode first: the video follows the mix")
        rec = self.st()
        if vd.plan_of(vp) is None or rec.get("basis") != mix["generation_id"]:
            vd.set_plan(db, vp, vd.build_plan(db, vp, self.ep))
            rec["basis"] = mix["generation_id"]
            self.put(rec)
        else:
            vd.sync_plan_shots(db, vp)
        n = len(vd.tracks_of(vp))
        self.tick(1.0, f"{n} camera track{'s' if n != 1 else ''} planned")
        return None

    def stage_render(self, _reviewed: bool):
        db, vp = self.db, self.vp
        rec = self.st()
        run = rec.get("run") or {}
        if run.get("job") != self.job.id:
            todo = vd.targets(db, vp, self.ep, self.quality, self.track_ids)
            run = {"job": self.job.id, "quality": self.quality, "targets": [t["id"] for t in todo], "gens": {}}
        by_id = {t["id"]: t for t in vd.tracks_of(vp)}
        ids = [tid for tid in run["targets"] if tid in by_id]
        if not ids:
            rec["run"] = run
            self.put(rec)
            return Skip("Every camera is up to date")
        inp = vd.Inputs(db, vp, self.ep)
        have = vd.takes_for(db, ids)
        waits, failed, done = [], [], 0
        for tid in ids:
            tries = run["gens"].setdefault(tid, [])
            g = db.get(Generation, tries[-1]) if tries else None
            if g is not None and g.status in vd.LIVE:
                waits.append(g.job_id)
                continue
            if g is not None and g.status in vd.FINISHED:
                done += 1
                continue
            if g is not None and len(tries) >= ATTEMPTS:
                failed.append(tid)
                continue
            base, idx = None, 0
            if g is not None and vd.first_unfinished(g) is not None:
                # the pieces already made stay; the retry picks up at the one that broke
                base, idx = g, vd.first_unfinished(g)
            elif g is None:
                newest = next(iter(have[tid][self.quality]), None)
                if self.from_piece and self.from_piece > 1 and newest is not None:
                    base, idx = newest, self.from_piece - 1
                elif newest is not None and newest.status == "failed" and vd.first_unfinished(newest) is not None \
                        and (newest.params or {}).get("sig") == inp.sig(by_id[tid], self.quality):
                    # a track that broke in an earlier run (or a restart) resumes too
                    base, idx = newest, vd.first_unfinished(newest)
            try:
                g = vd.enqueue_track(db, vp, self.ep, by_id[tid], self.quality, inp, self.job.id, base=base,
                                     from_idx=idx)
            except vd.VideoPodcastError as e:
                raise StageFailed(str(e)) from e
            db.flush()
            tries.append(g.id)
            waits.append(g.job_id)
            # commit per track: the next track's audio mix (ffmpeg) would otherwise run inside this write and
            # hold SQLite's lock past every other writer's busy_timeout. The run record goes in first so a
            # crash can't enqueue the track twice
            rec["run"] = run
            self.put(rec)
            self.save()
            db.commit()
        rec["run"] = run
        self.put(rec)
        if waits:
            return Wait(waits, self.render_message(ids, run, done))
        if failed:
            order = {t["id"]: n for n, t in enumerate(vd.tracks_of(vp), 1)}
            last = db.get(Generation, run["gens"][failed[0]][-1])
            job = db.get(Job, last.job_id or "") if last else None
            raise StageFailed(f"Camera track{'s' if len(failed) > 1 else ''} "
                              f"{', '.join(str(order[t]) for t in failed[:8])} wouldn't render: "
                              f"{(job.error if job else None) or 'no result'}")
        return None

    def render_message(self, ids: list[str], run: dict, done: int) -> str:
        pieces = made = 0
        for tid in ids:
            g = self.db.get(Generation, run["gens"][tid][-1])
            p = vd.progress(g) or {"pieces": 0, "done": 0}
            pieces, made = pieces + p["pieces"], made + p["done"]
        return f"{done} of {len(ids)} camera tracks rendered · piece {made} of {pieces}"

    def stage_assemble(self, _reviewed: bool):
        db, vp = self.db, self.vp
        rec = self.st()
        run = rec.get("run") or {}
        if run.get("job") == self.job.id and run.get("assemble_job"):
            child = db.get(Job, run["assemble_job"])
            if child is not None and child.status in ("queued", "running"):
                return Wait([child.id], "Cutting the episode")
            if child is not None and child.status == "done":
                res = child.result or {}
                outs = dict(vp.outputs or {})
                outs[self.quality] = {
                    "media_id": run["media_id"], "generation_id": child.generation_id, "quality": self.quality,
                    "built_at": utcnow().isoformat() + "Z",
                    **{k: res.get(k) for k in ("duration_s", "width", "height", "subtitles", "renderer",
                                                "fonts_missing", "fallback_shots")}}
                vp.outputs = outs
                flag_modified(vp, "outputs")
                return None
            raise StageFailed(f"Cutting the video failed: {(child.error if child else None) or 'the job disappeared'}")
        missing = self.missing_takes()
        if missing:
            raise StageFailed(f"Cut{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing[:8]))} "
                              "show a camera that hasn't been rendered yet; render first")
        item, g = vd.library_version(db, vp, self.ep, self.quality)
        child = Job(workspace_id=vp.workspace_id, type=vd.ASSEMBLE_JOB, generation_id=g.id, project_id=None,
                    message="Waiting for a worker",
                    payload={"video_podcast_id": vp.id, "quality": self.quality, "media_id": item.id,
                             "parent_job_id": self.job.id})
        db.add(child)
        db.flush()
        g.job_id = child.id
        rec["run"] = {"job": self.job.id, "assemble_job": child.id, "media_id": item.id}
        self.put(rec)
        return Wait([child.id], "Cutting the episode")

    def missing_takes(self) -> list[int]:
        have = vd.takes_for(self.db, [t["id"] for t in vd.tracks_of(self.vp)])
        out = []
        for n, c in enumerate(vd.cuts_of(self.vp), 1):
            for t, _a, _b in sources(self.vp, c):
                if t is not None and vd.take_to_cut(have[t["id"]], self.quality)[0] is None:
                    out.append(n)
                    break
        return out


def handle(ctx) -> dict:
    W = _w()
    run = Run(ctx)
    try:
        return run.run()
    except (W.JobDeferred, W.WorkerStopping):
        raise
    except W.JobCancelled:
        run.db.rollback()
        vp = run.db.get(VideoPodcast, run.vp.id)
        if vp is not None and run.current:
            rec = vd.stage(vp, run.current)
            if rec.get("status") == "running":
                rec.update(status="pending", detail="Cancelled")
                vd.put_stage(vp, run.current, rec)
        run.db.commit()
        raise
    except Exception as e:
        log.exception("video podcast %s failed in %s", run.vp.id, run.current)
        run.db.rollback()
        msg = str(e) or type(e).__name__
        if isinstance(e, LLMError):
            msg = f"The AI couldn't be reached: {msg}"
        vp = run.db.get(VideoPodcast, run.vp.id)
        if vp is not None and run.current:
            rec = vd.stage(vp, run.current)
            rec.update(status="failed", detail=msg[:500], finished_at=_iso())
            vd.put_stage(vp, run.current, rec)
        job = run.db.get(Job, run.job.id)
        job.result = {**(job.result or {}), "waiting_on": []}
        run.db.commit()
        label = dict(vd.STAGES).get(run.current or "", "AI video")
        raise RuntimeError(msg if msg.startswith(label) else f"{label} failed: {msg}") from e


# ---------------------------------------------------------------- assembly

def _work(job_id: str) -> Path:
    return job_workdir("vpodcast", job_id)


def out_size(aspect: str, quality: str) -> tuple[int, int]:
    table = HQ_SIZES if quality == "final" else VIDEO_SIZES
    return table.get(aspect, table["16:9"])


def cover_card(db, vp: VideoPodcast, ep, size: tuple[int, int], dst: Path) -> Path:
    from app.podcast import publish  # the Audiogram's cover rule: this episode's, else the show's

    st = vd.settings_of(vp)
    cover_id = st.get("cover_media_id") or publish.cover_for(db, ep, None)
    item = db.get(MediaItem, cover_id or "") if cover_id else None
    g = db.get(Generation, item.generation_id or "") if item is not None else None
    path = generation_file(g) if g is not None else None
    show = db.get(PodcastShow, ep.show_id) if ep.show_id else None
    assets = br.KitAssets(name=ep.title, tagline=show.name if show else "",
                          logo=path if path is not None and path.is_file() else None)
    br.render_card(size, assets, CardSettings(enabled=True, show_name=True, show_tagline=True)).save(dst)
    return dst


def sources(vp: VideoPodcast, cut: dict) -> list[tuple[dict | None, float, float]]:
    """Where a cut's picture comes from: (track, start, end) on the mix clock, None for the cover. A host cut
    that crosses a segment edge takes each part from that segment's own track."""
    a, b = cut["start"], cut["end"]
    if cut["camera"] == vplan.COVER:
        return [(None, a, b)]
    tracks = [t for t in vd.tracks_of(vp) if t["camera"] == cut["camera"]]
    if cut["camera"] == vplan.WIDE:
        t = next((t for t in tracks if t["start"] - 0.05 <= a and b <= t["end"] + 0.05), None) or             next((t for t in tracks if t["start"] < b and t["end"] > a), None)
        if t is None:
            raise vd.VideoPodcastError(f"No wide take rolls at {a:.1f} s; rebuild the cut list")
        return [(t, a, b)]
    out = [(t, max(a, t["start"]), min(b, t["end"])) for t in sorted(tracks, key=lambda t: t["start"])
           if t["start"] < b - 1e-6 and t["end"] > a + 1e-6]
    if not out:
        raise vd.VideoPodcastError(f"No camera track for the cut at {a:.1f} s; rebuild the cut list")
    return out


def cues_for(timeline: list[dict]) -> list[tuple[float, float, str]]:
    segs = [{"start": t["start"], "end": t["end"], "text": t["text"]} for t in timeline
            if (t.get("text") or "").strip() and t.get("kind") in vplan.TALK_KINDS]
    return tr.cues(segs)


def handle_assemble(ctx) -> dict:
    from app import subtitles as subs

    db, job = ctx.db, ctx.job
    p = job.payload or {}
    gen = db.get(Generation, job.generation_id or "")
    vp = db.get(VideoPodcast, p.get("video_podcast_id") or "")
    if gen is None or vp is None:
        raise vd.VideoPodcastError("The AI video or its Library record no longer exists")
    ep = vd.episode_of(db, vp)
    quality = p.get("quality") or "draft"
    mix = vd.mix_of(ep)
    audio = generation_file(db.get(Generation, (mix or {}).get("generation_id") or ""))
    if mix is None or audio is None or not audio.is_file():
        raise vd.VideoPodcastError("The episode's mix is gone; mix it again")
    gen.status = "generating"
    db.commit()
    st = vd.settings_of(vp)
    w, h = out_size(st["aspect"], quality)
    cuts = vd.cuts_of(vp)
    have = vd.takes_for(db, [t["id"] for t in vd.tracks_of(vp)])
    total = aa.stream_info(audio).duration
    folder, rel_folder = lib.media_dir(gen.workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    work = _work(job.id)
    dst, part = (folder / f"{gen.id}.mp4").resolve(), work / "part.mp4"
    fallback: list[int] = []
    t0 = time.monotonic()
    try:
        clips = []
        card = None
        for n, cut in enumerate(cuts, 1):
            ctx.progress(0.05 + 0.6 * n / max(1, len(cuts)), f"Cutting {n} of {len(cuts)}")
            for k, (track, a, b) in enumerate(sources(vp, cut)):
                dur = round(b - a, 4)
                if dur <= 0:
                    continue
                trim = 0.0
                if track is None:
                    card = card or cover_card(db, vp, ep, (w, h), work / "cover.png")
                    src = card
                else:
                    g, fb = vd.take_to_cut(have[track["id"]], quality)
                    src = generation_file(g) if g is not None else None
                    if src is None or not src.is_file():
                        raise vd.VideoPodcastError(f"Cut {n} shows a camera that hasn't been rendered; render first")
                    if fb and n not in fallback:
                        fallback.append(n)
                    # every track runs on the mix clock from its own start, so this is the same moment
                    trim = max(0.0, a - track["start"])
                out = work / f"c{n:04d}_{k}.mp4"
                # muted on purpose: the episode mix is the soundtrack, not the takes' own audio
                rl.run_ff(rl.normalise_cmd(src, out, size=(w, h), trim_in=trim, duration=dur, has_audio=False))
                clips.append((out, dur))
        lst = rl.write_concat_list(work, [rl.Entry(c) for c, _ in clips])
        joined = work / "joined.mp4"
        want = sum(d for _, d in clips)
        try:
            rl.run_ff(rl.concat_copy_cmd(lst, joined))
            if abs(rl.probe(joined).duration - want) > max(0.3, 0.02 * len(clips)):
                raise rl.FFmpegError("copy join came out the wrong length")
        except rl.FFmpegError as e:
            log.warning("stream-copy join failed, re-encoding: %s", e)
            rl.run_ff(rl.concat_reencode_cmd(lst, joined))

        mode = st.get("subtitles") or "none"
        cues = cues_for(mix.get("timeline") or []) if mode != "none" else []
        picture, renderer, missing, subs_done = joined, None, [], "none"
        if mode == "burn" and cues:
            ctx.progress(0.7, "Burning in the subtitles")
            try:
                from app import overlays

                res = overlays.burn_captions(joined, work / "burned.mp4", cues, style=st.get("caption_style") or "classic",
                                             language=ep.language, width=w, height=h, timeout=7200)
                picture, renderer, missing, subs_done = work / "burned.mp4", res.get("renderer"), \
                    list(res.get("fonts_missing") or []), "burn"
            except RuntimeError as e:
                # a video without its subtitles beats no video
                log.warning("subtitle burn failed: %s", e)
                subs_done = "failed"
        ctx.progress(0.85, "Laying the episode mix underneath")
        chapters = [(c["title"], float(c["start"]), float(c["end"])) for c in mix.get("chapters") or []]
        meta = rl.write_chapters(work, ep.title, chapters)
        args = ["-i", str(picture), "-i", str(audio), "-f", "ffmetadata", "-i", str(meta)]
        tail: list[str] = []
        maps = ["-map", "0:v", "-map", "1:a"]
        if mode == "soft" and cues:
            srt = subs.write_srt(work / "subs.srt", cues, ep.language)
            args += ["-i", str(srt)]
            maps += ["-map", "3:0"]
            tail = ["-c:s", "mov_text", *subs.soft_track_args(0, LANG3.get(ep.language, "und"), "Subtitles")]
            subs_done = "soft"
        rl.run_ff([*args, *maps, "-map_metadata", "2", "-map_chapters", "2", "-c:v", "copy", *rl.AENC, *tail,
                   "-t", f"{total:.3f}", "-movflags", "+faststart", str(part)], timeout=7200)
        part.replace(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.refresh(gen)
    rel = f"{rel_folder}/{dst.name}"
    dur = round(rl.probe(dst).duration, 3)
    gen.params = {**(gen.params or {}), "duration_s": dur, "subtitles": subs_done, "width": w, "height": h}
    if missing:
        gen.params["fonts_missing"] = missing
    gen.file_path, gen.media_type, gen.status = rel, "video/mp4", "ready"
    return {"file_path": rel, "media_type": "video/mp4", "duration_s": dur, "width": w, "height": h,
            "subtitles": subs_done, "renderer": renderer, "fonts_missing": missing, "fallback_shots": fallback,
            "cuts": len(cuts), "seconds": round(time.monotonic() - t0, 3)}


def queue(db, vp: VideoPodcast, run: str, *, quality: str | None = None, track_ids: list[str] | None = None,
          from_piece: int | None = None) -> Job:
    vd.ensure_idle(db, vp)
    payload = {"video_podcast_id": vp.id, "run": run}
    if quality:
        payload["quality"] = quality
    if track_ids:
        payload["track_ids"] = track_ids
    if from_piece:
        payload["from_piece"] = from_piece
    label = {"continue": "AI video", "render": f"AI video {quality} render",
             "assemble": f"AI video {quality} cut"}[run]
    job = Job(workspace_id=vp.workspace_id, type=vd.JOB, project_id=vp.project_id,
              message=f"Waiting for a worker · {label}", payload=payload)
    db.add(job)
    db.flush()
    vp.job_id = job.id
    return job
