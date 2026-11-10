"""Stems (contract v21): split a Library track, or a video's sound, into its parts.

"two" is vocals + instrumental with the vocal model Dub already uses; "four" runs a Demucs model for
vocals, drums, bass and other. Each stem becomes a Library audio item of its own (FLAC, 48 kHz) tagged
stems:<source id> so the source's earlier splits can be found again. One `audio_stems` job per split, on
the audio lane.
"""
import logging
import shutil
import time
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app import audio_analysis as aa
from app import audio_jobs as aj
from app import library as lib
from app import thumbs
from app.config import get_settings
from app.dub import engines
from app.models import Generation, Job, MediaItem, new_id
from app.services import generation_file, job_out, new_seed

log = logging.getLogger("mixai.stems")

JOB = "audio_stems"
TAG = "stems"
RATE = 48000
MODES = {"two": ("vocals", "instrumental"), "four": ("vocals", "drums", "bass", "other")}
LABELS = {"vocals": "Vocals", "instrumental": "Instrumental", "drums": "Drums", "bass": "Bass", "other": "Other",
          "guitar": "Guitar", "piano": "Piano"}
ACTIVE = ("queued", "running")


class StemsError(RuntimeError):
    pass


def source_tag(item_id: str) -> str:
    # 6 + 32 characters, inside the Library's 40-character tag limit
    return f"{TAG}:{item_id.replace('-', '')}"


def label(stem: str) -> str:
    return LABELS.get(stem, stem.replace("_", " ").capitalize())


def model_for(mode: str) -> str:
    s = get_settings()
    return s.stems_four_model if mode == "four" else s.dub_separation_model


def source(db: Session, workspace_id: str, item_id: str, version: str | None = None
           ) -> tuple[MediaItem, Generation]:
    item = db.get(MediaItem, item_id) if item_id else None
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(404, "Track not found")
    if item.kind not in ("audio", "video"):
        raise HTTPException(422, "Pick an audio clip or a video to split")
    g = aj._version(db, item, version)
    aj._usable(g, f"{item.kind}/", "an audio clip" if item.kind == "audio" else "a video")
    if not aa.stream_info(generation_file(g)).has_audio:
        raise HTTPException(422, "This video has no sound to split" if item.kind == "video"
                            else "This clip has no audio stream to split")
    return item, g


def active_jobs(db: Session, workspace_id: str, item_id: str) -> list[Job]:
    rows = db.scalars(select(Job).where(Job.workspace_id == workspace_id, Job.type == JOB,
                                        Job.status.in_(ACTIVE)).order_by(Job.created_at.desc())).all()
    return [j for j in rows if (j.payload or {}).get("source_id") == item_id]


def queue(db: Session, item: MediaItem, g: Generation, mode: str, user_id: str | None, lyrics: bool = False) -> Job:
    if any((j.payload or {}).get("mode") == mode for j in active_jobs(db, item.workspace_id, item.id)):
        raise HTTPException(409, "This track is already being split that way")
    job = Job(workspace_id=item.workspace_id, type=JOB, message="Waiting for a worker · Stems",
              payload={"source_id": item.id, "generation_id": g.id, "mode": mode, "model": model_for(mode),
                       "user_id": user_id, "lyrics": lyrics})
    db.add(job)
    db.flush()
    return job


def _order(stems: dict, mode: str) -> list[str]:
    known = [s for s in MODES[mode] if s in stems]
    return known + sorted(s for s in stems if s not in known)


def handle(ctx) -> dict:
    db, job = ctx.db, ctx.job
    p = job.payload or {}
    mode = p.get("mode") if p.get("mode") in MODES else "two"
    item = db.get(MediaItem, p.get("source_id") or "")
    g = db.get(Generation, p.get("generation_id") or "")
    src = generation_file(g) if g is not None else None
    if item is None or src is None or not src.is_file():
        raise StemsError("The track to split is gone")
    model = p.get("model") or model_for(mode)
    s = get_settings()
    work = s.data_dir / "workspaces" / job.workspace_id / "stems" / job.id
    folder, rel_folder = lib.media_dir(job.workspace_id)
    folder.mkdir(parents=True, exist_ok=True)
    written: list = []
    t0 = time.monotonic()
    shutil.rmtree(work, ignore_errors=True)
    try:
        work.mkdir(parents=True)
        ctx.progress(0.02, "Reading the sound")
        # one decode for audio and video alike: the models want plain stereo PCM
        wav = work / "source.wav"
        try:
            engines._ffmpeg(["-i", str(src), "-vn", "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le", str(wav)])
        except RuntimeError as e:
            raise StemsError(f"Couldn't read the sound of “{item.title or 'this track'}”: {e}") from None
        if not aa.stream_info(wav).has_audio:
            raise StemsError("There's no audio stream to split")

        driver = ctx.driver_factory()
        fn = getattr(driver, "separate_stems" if mode == "four" else "separate", None)
        if fn is None:
            raise StemsError(f"The {getattr(driver, 'name', '?')} driver can't split stems")
        res = fn(wav, work / "out", model, lambda f, m: ctx.progress(0.05 + 0.8 * f, m or "Separating the stems"))
        stems = {k.lower(): v for k, v in res.items() if v is not None}
        missing = [x for x in MODES[mode] if x not in stems]
        if missing:
            raise StemsError(f"The model didn't write {', '.join(label(x).lower() for x in missing)}")

        words = None
        if p.get("lyrics") and stems.get("vocals"):
            ctx.progress(0.8, "Writing out the lyrics")
            try:
                heard = driver.transcribe(Path(stems["vocals"]), None,
                                          lambda f, m: ctx.progress(0.8 + 0.05 * f, "Writing out the lyrics"))
                words = lyrics_text(heard)
            except Exception as e:
                # the split is what was asked for; lyrics are a bonus
                log.warning("lyrics for split %s failed: %s", job.id, e)

        names = _order(stems, mode)
        files = []
        for i, name in enumerate(names):
            ctx.progress(0.86 + 0.12 * i / len(names), f"Saving {label(name).lower()}")
            gid = new_id()
            dst = folder / f"{gid}.flac"
            engines._ffmpeg(["-i", str(stems[name]), "-ar", str(RATE), "-c:a", "flac", str(dst)])
            written.append(dst)
            files.append((name, gid, dst, round(aa.stream_info(dst).duration, 3)))
    except BaseException:
        for f in written:
            f.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # no progress ticks from here on: they commit, and a half-made split must roll back as one
    base = (item.title or "Untitled")[:240]
    tags = lib.clean_tags([TAG, source_tag(item.id)])
    made = []
    for name, gid, dst, dur in files:
        title = f"{base} — {label(name)}"
        stem_item = MediaItem(id=new_id(), workspace_id=job.workspace_id, kind="audio", origin="generated",
                              title=title, tags=list(tags), duration_s=dur, folder_id=item.folder_id)
        db.add(stem_item)
        db.flush()
        extra = {"lyrics": words} if name == "vocals" and words else {}
        sg = Generation(id=gid, workspace_id=job.workspace_id, project_id=None, target_type="media",
                        target_id=stem_item.id, kind="audio", version=1, status="ready", prompt=title,
                        seed=new_seed(), job_id=job.id, media_type="audio/flac",
                        file_path=f"{rel_folder}/{dst.name}",
                        params={"kind": "stem", "stem": name, "source_id": item.id, "source_generation_id": g.id,
                                "mode": mode, "model": model, "split_id": job.id, "duration_s": dur,
                                "created_by": {"user_id": p.get("user_id"), "flow": "stems"}, **extra})
        db.add(sg)
        db.flush()
        stem_item.generation_id = sg.id
        lib.touch(stem_item)
        made.append((name, stem_item.id, sg.id))
    out = {"mode": mode, "model": model, "source_id": item.id, "items": [m[1] for m in made],
           "generation_ids": [m[2] for m in made], "stems": {m[0]: m[1] for m in made},
           "seconds": round(time.monotonic() - t0, 3)}
    # in the result too, so a job poller (MCP get_job) gets them without listing the splits
    if words:
        out["lyrics"] = words
    return out


def lyrics_text(heard: dict) -> str:
    """One line per sung phrase, the way lyrics are written."""
    lines = [" ".join((s.get("text") or "").split()) for s in heard.get("segments") or []]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines) if lines else (heard.get("text") or "").strip()


def on_job_finished(db, job) -> None:
    """Worker hook: the waveform and loudness of every stem, as the hooks keyed on job.generation_id do
    for single outputs."""
    if job.type != JOB or job.status != "done":
        return
    for gid in (job.result or {}).get("generation_ids") or []:
        g = db.get(Generation, gid)
        if g is None or not g.file_path:
            continue
        try:
            thumbs.for_generation(g)
            aa.analyse(get_settings().data_dir / g.file_path)
        except Exception:
            log.warning("couldn't analyse stem %s", gid, exc_info=True)


# ---------------------------------------------------------------- listing

def splits_of(db: Session, workspace_id: str, item_id: str) -> dict:
    tag = source_tag(item_id)
    rows = db.scalars(select(MediaItem).where(MediaItem.workspace_id == workspace_id, MediaItem.kind == "audio",
                                              cast(MediaItem.tags, String).like(f'%"{tag}"%'))).all()
    gens = {g.id: g for g in db.scalars(select(Generation).where(
        Generation.id.in_([i.generation_id for i in rows if i.generation_id])))} if rows else {}
    outs = {o.id: o for o in lib.items_out(db, list(rows))}
    groups: dict[str, dict] = {}
    for i in rows:
        gp = (gens.get(i.generation_id).params if gens.get(i.generation_id) else None) or {}
        # an item edited later keeps its stem facts on its first version only; fall back to that
        if gp.get("kind") != "stem":
            first = db.scalars(select(Generation).where(Generation.target_type == "media", Generation.target_id == i.id)
                               .order_by(Generation.version)).first()
            gp = (first.params if first is not None else None) or {}
        key = gp.get("split_id") or i.id
        grp = groups.setdefault(key, {"id": key, "mode": gp.get("mode") or "two", "created_at": i.created_at,
                                      "items": []})
        grp["created_at"] = min(grp["created_at"], i.created_at)
        grp["items"].append((gp.get("stem") or "", outs[i.id]))
        if gp.get("lyrics"):
            grp["lyrics"] = gp["lyrics"]
    splits = []
    for grp in groups.values():
        order = MODES.get(grp["mode"], ())
        grp["items"].sort(key=lambda x: (order.index(x[0]) if x[0] in order else len(order), x[0]))
        grp.setdefault("lyrics", None)
        splits.append({**grp, "stems": [s for s, _ in grp["items"]], "items": [o for _, o in grp["items"]]})
    splits.sort(key=lambda x: x["created_at"], reverse=True)
    return {"source_id": item_id, "splits": splits, "items": [o for sp in splits for o in sp["items"]],
            "jobs": [job_out(j) for j in active_jobs(db, workspace_id, item_id)]}
