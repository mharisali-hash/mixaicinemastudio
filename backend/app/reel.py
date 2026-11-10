"""The Reel: approved takes in film order, and the cached two-level assembly (contract v3).

Level 1 is one mezzanine mp4 per scene (clips normalised, joined with their transitions).
Level 2 joins the mezzanines with the concat demuxer and -c copy; scene-boundary
transitions become short seam clips cut from the neighbouring mezzanines' tail/head.
Every mezzanine forces IDR frames over its first ~2 s so the demuxer can start it at
any seam inpoint without dragging in earlier frames.
"""
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import storyboard as sb
from app import title_intro
from app.config import get_settings
from app.models import Generation, Job, Project, Reel, ReelClip, Scene, new_id, utcnow
from app.schemas import (
    MezzanineOut, ReelClipOut, ReelMissingOut, ReelOut, ReelSceneOut, RenderOut,
)
from app.services import gen_out, generation_file, media_url, next_version
from app.workdirs import job_workdir

log = logging.getLogger("mixai.reel")

FPS = 24
# bump when the encode recipe changes so every cached mezzanine goes stale
PIPELINE = "reel-v1"
MIN_TRANSITION = 0.05
MAX_TRANSITION = 2.0
MIN_CLIP = 0.25
HEAD_IDR_S = MAX_TRANSITION + 0.1
LUFS = -16.0
ASSEMBLE_JOB = "reel_assemble"

VENC = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-profile:v", "high", "-level", "4.0",
    "-pix_fmt", "yuv420p", "-bf", "0", "-r", str(FPS), "-video_track_timescale", "12288",
]
AENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


class FFmpegError(RuntimeError):
    pass


# ---------------------------------------------------------------- ffmpeg plumbing

def ffmpeg() -> str:
    return get_settings().ffmpeg_path()


def run_ff(args: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    # TODO: run under Popen and kill on cancel; today a cancel waits for the current ffmpeg step
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        # the first error lines say why; the tail is mostly "Terminating thread" noise
        why = [ln for ln in r.stderr.splitlines() if "rror" in ln or "nvalid" in ln][:6]
        raise FFmpegError("ffmpeg failed: " + (" | ".join(why) or r.stderr[-600:]))
    return r


@dataclass
class Probe:
    duration: float = 0.0
    has_audio: bool = False
    has_video: bool = False
    width: int = 0
    height: int = 0
    chapters: list[tuple[float, float]] = field(default_factory=list)


_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID = re.compile(r"Stream #\S+.*?: Video: .*?, (\d{2,5})x(\d{2,5})")
_CHAP = re.compile(r"Chapter #\S+: start (-?\d+(?:\.\d+)?), end (-?\d+(?:\.\d+)?)")


def probe(path: Path) -> Probe:
    # imageio-ffmpeg ships no ffprobe, so read what `ffmpeg -i` prints
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    err = r.stderr
    p = Probe()
    m = _DUR.search(err)
    if m:
        p.duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = _VID.search(err)
    if v:
        p.has_video, p.width, p.height = True, int(v[1]), int(v[2])
    p.has_audio = bool(re.search(r"Stream #\S+.*?: Audio:", err))
    p.chapters = [(float(a), float(b)) for a, b in _CHAP.findall(err)]
    return p


def frames(seconds: float) -> float:
    """Snap to whole frames so xfade offsets and concat in/out points line up."""
    return round(seconds * FPS) / FPS


def clamp_transition(transition_s: float, left_s: float, right_s: float) -> float:
    d = min(transition_s, min(left_s, right_s) / 3, MAX_TRANSITION)
    return max(MIN_TRANSITION, d)


def seam_length(kind: str, transition_s: float, left_s: float, right_s: float) -> float:
    """How much a join eats from the timeline: dissolves overlap, fades and cuts don't."""
    if kind == "dissolve":
        return frames(clamp_transition(transition_s, left_s, right_s))
    return 0.0


# ---------------------------------------------------------------- command builders

def normalise_cmd(src: Path, dst: Path, *, size: tuple[int, int], trim_in: float, duration: float,
                  has_audio: bool) -> list[str]:
    w, h = size
    v = (
        f"[0:v]trim=start={trim_in:.4f}:duration={duration:.4f},setpts=PTS-STARTPTS,fps={FPS},"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration={duration:.4f}[v]"
    )
    args = ["-i", str(src)]
    if has_audio:
        a = (f"[0:a]atrim=start={trim_in:.4f}:duration={duration:.4f},asetpts=PTS-STARTPTS,"
             f"aresample=48000,{AFMT},apad=whole_dur={duration:.4f}[a]")
    else:
        # silence keeps every clip's audio track present, so joins never drop the audio stream
        args += ["-f", "lavfi", "-t", f"{duration:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
        a = f"[1:a]{AFMT}[a]"
    return [*args, "-filter_complex", f"{v};{a}", "-map", "[v]", "-map", "[a]", *VENC, *AENC,
            "-t", f"{duration:.4f}", str(dst)]


@dataclass
class Item:
    """One normalised clip as the mezzanine graph sees it."""
    duration: float
    transition: str = "cut"
    transition_s: float = 0.5


def mezzanine_graph(items: list[Item], hard_cuts: bool = False) -> tuple[str, float]:
    """filter_complex joining inputs 0..n-1 with their transitions. Returns (graph, length_s).

    The first item's transition belongs to the film-level seam, so it's ignored here.
    """
    parts = [f"[{i}:v]setpts=PTS-STARTPTS,fps={FPS}[v{i}];[{i}:a]asetpts=PTS-STARTPTS,{AFMT}[a{i}]"
             for i in range(len(items))]
    cv, ca, length = "v0", "a0", items[0].duration
    for i in range(1, len(items)):
        it, prev = items[i], items[i - 1]
        kind = "cut" if hard_cuts else it.transition
        ov, oa = f"vj{i}", f"aj{i}"
        if kind == "dissolve":
            d = frames(clamp_transition(it.transition_s, prev.duration, it.duration))
            parts.append(
                f"[{cv}][v{i}]xfade=transition=fade:duration={d:.4f}:offset={length - d:.4f}[{ov}];"
                f"[{ca}][a{i}]acrossfade=d={d:.4f}:c1=tri:c2=tri[{oa}]"
            )
            length += it.duration - d
        elif kind == "fade_black":
            half = frames(clamp_transition(it.transition_s, prev.duration, it.duration)) / 2
            st = length - half
            parts.append(
                f"[{cv}]fade=t=out:st={st:.4f}:d={half:.4f}[fo{i}];[{ca}]afade=t=out:st={st:.4f}:d={half:.4f}[afo{i}];"
                f"[v{i}]fade=t=in:st=0:d={half:.4f}[fi{i}];[a{i}]afade=t=in:st=0:d={half:.4f}[afi{i}];"
                f"[fo{i}][afo{i}][fi{i}][afi{i}]concat=n=2:v=1:a=1[{ov}][{oa}]"
            )
            length += it.duration
        else:
            parts.append(f"[{cv}][{ca}][v{i}][a{i}]concat=n=2:v=1:a=1[{ov}][{oa}]")
            length += it.duration
        cv, ca = ov, oa
    parts.append(f"[{cv}]null[vout];[{ca}]anull[aout]")
    return ";".join(parts), length


def mezzanine_cmd(inputs: list[Path], items: list[Item], dst: Path, hard_cuts: bool = False) -> tuple[list[str], float]:
    graph, length = mezzanine_graph(items, hard_cuts)
    args: list[str] = []
    for p in inputs:
        args += ["-i", str(p)]
    args += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", *VENC, *AENC,
             "-force_key_frames", f"expr:lt(t,{HEAD_IDR_S})", "-forced-idr", "1",
             "-movflags", "+faststart", str(dst)]
    return args, length


def seam_cmd(kind: str, left: Path, left_len: float, right: Path, d: float, dst: Path) -> list[str]:
    """A short clip bridging two mezzanines: dissolve uses tail+head of length d, fade_black d/2 each."""
    take = d if kind == "dissolve" else d / 2
    args = ["-ss", f"{left_len - take:.4f}", "-t", f"{take:.4f}", "-i", str(left),
            "-t", f"{take:.4f}", "-i", str(right)]
    pre = (f"[0:v]setpts=PTS-STARTPTS,fps={FPS}[l];[1:v]setpts=PTS-STARTPTS,fps={FPS}[r];"
           f"[0:a]asetpts=PTS-STARTPTS,{AFMT}[la];[1:a]asetpts=PTS-STARTPTS,{AFMT}[ra];")
    if kind == "dissolve":
        g = pre + (f"[l][r]xfade=transition=fade:duration={d:.4f}:offset=0[vout];"
                   f"[la][ra]acrossfade=d={d:.4f}:c1=tri:c2=tri[aout]")
    else:
        g = pre + (f"[l]fade=t=out:st=0:d={take:.4f}[lo];[la]afade=t=out:st=0:d={take:.4f}[lao];"
                   f"[r]fade=t=in:st=0:d={take:.4f}[ri];[ra]afade=t=in:st=0:d={take:.4f}[rai];"
                   f"[lo][lao][ri][rai]concat=n=2:v=1:a=1[vout][aout]")
    return [*args, "-filter_complex", g, "-map", "[vout]", "-map", "[aout]", *VENC, *AENC,
            "-movflags", "+faststart", str(dst)]


def _q(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", r"'\''") + "'"


@dataclass
class Entry:
    path: Path
    inpoint: float | None = None
    outpoint: float | None = None


def write_concat_list(workdir: Path, entries: list[Entry], name: str = "film.ffconcat") -> Path:
    # always inside the job's own workdir: two assemblies must never share a list file
    lines = ["ffconcat version 1.0"]
    for e in entries:
        lines.append(f"file {_q(e.path)}")
        if e.inpoint:
            lines.append(f"inpoint {e.inpoint:.4f}")
        if e.outpoint is not None:
            lines.append(f"outpoint {e.outpoint:.4f}")
    p = workdir / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def concat_copy_cmd(list_path: Path, dst: Path) -> list[str]:
    return ["-f", "concat", "-safe", "0", "-i", str(list_path), "-map", "0:v", "-map", "0:a",
            "-c", "copy", "-movflags", "+faststart", str(dst)]


def concat_reencode_cmd(list_path: Path, dst: Path) -> list[str]:
    return ["-f", "concat", "-safe", "0", "-i", str(list_path), "-map", "0:v", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dst)]


def _meta_escape(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def write_chapters(workdir: Path, title: str, chapters: list[tuple[str, float, float]]) -> Path:
    lines = [";FFMETADATA1", f"title={_meta_escape(title)}"]
    for name, start, end in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(round(start * 1000))}",
                  f"END={int(round(end * 1000))}", f"title={_meta_escape(name)}"]
    p = workdir / "chapters.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def measure_loudness(src: Path) -> dict | None:
    r = run_ff(["-i", str(src), "-vn", "-af", f"loudnorm=I={LUFS}:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "-"])
    blob = r.stderr[r.stderr.rfind("{"): r.stderr.rfind("}") + 1]
    try:
        stats = json.loads(blob)
        level = float(stats["input_i"])
    except (ValueError, KeyError):
        return None
    # pure silence measures -inf; there's nothing to normalise
    if level != level or level < -70:
        return None
    return stats


def finish_cmd(src: Path, chapters: Path, dst: Path, stats: dict | None) -> list[str]:
    args = ["-i", str(src), "-f", "ffmetadata", "-i", str(chapters), "-map", "0:v", "-map", "0:a",
            "-map_metadata", "1", "-map_chapters", "1", "-c:v", "copy"]
    if stats:
        ln = (f"loudnorm=I={LUFS}:TP=-1.5:LRA=11:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
              f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
              f"offset={stats['target_offset']}:linear=true:print_format=summary")
        # loudnorm works at 192 kHz internally
        args += ["-af", f"{ln},aresample=48000", *AENC]
    else:
        args += ["-c:a", "copy"]
    return [*args, "-movflags", "+faststart", str(dst)]


# ---------------------------------------------------------------- reel data

def get_reel(db: Session, project: Project) -> Reel:
    reel = db.scalars(select(Reel).where(Reel.project_id == project.id)).first()
    if reel is not None:
        return reel
    reel = Reel(workspace_id=project.workspace_id, project_id=project.id, title=project.title, settings={})
    db.add(reel)
    try:
        db.flush()
    except IntegrityError:
        # a parallel request created it first
        db.rollback()
        reel = db.scalars(select(Reel).where(Reel.project_id == project.id)).one()
    return reel


def _approved_takes(db: Session, project_id: str) -> dict[str, Generation]:
    rows = db.scalars(select(Generation).where(
        Generation.project_id == project_id, Generation.target_type == "shot",
        Generation.kind == "take", Generation.status == "approved",
    )).all()
    return {g.target_id: g for g in rows}


def _clips(db: Session, reel_id: str) -> list[ReelClip]:
    return list(db.scalars(select(ReelClip).where(ReelClip.reel_id == reel_id)
                           .order_by(ReelClip.order, ReelClip.created_at)).all())


def source_duration(g: Generation) -> float:
    f = generation_file(g)
    if f is not None and f.exists():
        d = probe(f).duration
        if d > 0:
            return round(d, 3)
    return float((g.params or {}).get("duration_s") or 4.0)


def sync(db: Session, reel: Reel) -> bool:
    """Bring clips in line with the approved takes. Keeps order/trims/transitions. Returns True if anything moved."""
    shots = sb.project_shots(db, reel.project_id)
    rank = {s.id: i for i, s in enumerate(shots)}
    takes = _approved_takes(db, reel.project_id)
    clips = {c.shot_id: c for c in _clips(db, reel.id)}
    touched: set[str] = set()
    fresh: set[str] = set()
    for s in shots:
        g, c = takes.get(s.id), clips.get(s.id)
        if c is None:
            if g is None:
                continue
            c = ReelClip(workspace_id=reel.workspace_id, reel_id=reel.id, shot_id=s.id, scene_id=s.scene_id,
                         generation_id=g.id, order=0, transition_in="cut", transition_s=0.5,
                         source_duration_s=source_duration(g))
            db.add(c)
            clips[s.id] = c
            fresh.add(s.id)
            touched.add(s.scene_id)
            continue
        if c.scene_id != s.scene_id:
            touched |= {c.scene_id, s.scene_id}
            c.scene_id = s.scene_id
        # no approved take: the clip keeps its edits and simply sits out until one is approved again
        if g is not None and c.generation_id != g.id:
            c.generation_id = g.id
            c.changed = True
            c.source_duration_s = source_duration(g)
            if c.trim_in_s + c.trim_out_s >= c.source_duration_s - MIN_CLIP:
                c.trim_in_s = c.trim_out_s = 0.0
            touched.add(s.scene_id)
        elif g is not None and c.source_duration_s is None:
            c.source_duration_s = source_duration(g)

    for scene_id in touched:
        mine = [c for c in clips.values() if c.scene_id == scene_id]
        kept = sorted((c for c in mine if c.shot_id not in fresh), key=lambda c: (c.order, rank.get(c.shot_id, 0)))
        for c in sorted((c for c in mine if c.shot_id in fresh), key=lambda c: rank.get(c.shot_id, 0)):
            # slot a new clip before the first kept clip whose shot comes later in the storyboard
            at = next((i for i, k in enumerate(kept) if rank.get(k.shot_id, 0) > rank[c.shot_id]), len(kept))
            kept.insert(at, c)
        for i, c in enumerate(kept, start=1):
            c.order = i
    if touched:
        reel.updated_at = utcnow()
    db.flush()
    return bool(touched)


@dataclass
class ClipPlan:
    clip: ReelClip
    gen: Generation
    source: float
    duration: float


@dataclass
class ScenePlan:
    scene: Scene
    clips: list[ClipPlan]
    hash: str
    length: float
    mezzanine: Generation | None = None
    fresh: bool = False

    @property
    def head(self) -> ReelClip | None:
        return self.clips[0].clip if self.clips else None


def clip_duration(source: float, trim_in: float, trim_out: float) -> float:
    return frames(max(MIN_CLIP, source - trim_in - trim_out))


def reel_size(db: Session, reel: Reel) -> tuple[int, int]:
    s = (reel.settings or {}).get("size")
    if s and len(s) == 2:
        # handy for tests and tiny previews; must stay even for yuv420p
        return int(s[0]) // 2 * 2, int(s[1]) // 2 * 2
    project = db.get(Project, reel.project_id)
    if (project.settings or {}).get("video_quality") == "hq":
        # an hq film's takes are 1280x704; stitching at the draft size threw that away (Ruslan's explainer, 23 Oct)
        from app.drivers.comfy import HQ_SIZES

        return HQ_SIZES.get(project.aspect_ratio, HQ_SIZES["16:9"])
    return sb.draft_size(project.aspect_ratio)


def scene_hash(clips: list[ClipPlan], size: tuple[int, int]) -> str:
    rows = []
    for i, cp in enumerate(clips):
        c = cp.clip
        # the first clip's transition is the film-level seam; changing it must not rebuild this scene
        trans = ("-", 0.0) if i == 0 else (c.transition_in, round(c.transition_s, 3))
        rows.append([c.generation_id, round(c.trim_in_s, 3), round(c.trim_out_s, 3), *trans])
    raw = json.dumps({"p": PIPELINE, "size": list(size), "clips": rows}, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _latest_mezzanines(db: Session, scene_ids: list[str]) -> dict[str, Generation]:
    if not scene_ids:
        return {}
    rows = db.scalars(select(Generation).where(
        Generation.target_type == "scene", Generation.target_id.in_(scene_ids), Generation.kind == "mezzanine",
        Generation.status.in_(("ready", "approved")),
    ).order_by(Generation.created_at.desc())).all()
    out: dict[str, Generation] = {}
    for g in rows:
        out.setdefault(g.target_id, g)
    return out


def plan(db: Session, reel: Reel) -> tuple[list[ScenePlan], list[ReelMissingOut]]:
    """Read-only view of what the film is made of right now."""
    scenes = list(db.scalars(select(Scene).where(Scene.project_id == reel.project_id)
                             .order_by(Scene.order, Scene.created_at)).all())
    takes = _approved_takes(db, reel.project_id)
    by_scene: dict[str, list[ReelClip]] = {}
    for c in _clips(db, reel.id):
        by_scene.setdefault(c.scene_id, []).append(c)
    clipped = {c.shot_id for cs in by_scene.values() for c in cs}
    size = reel_size(db, reel)
    mezz = _latest_mezzanines(db, [s.id for s in scenes])

    missing: list[ReelMissingOut] = []
    for s in sb.project_shots(db, reel.project_id):
        if s.id not in takes:
            missing.append(ReelMissingOut(shot_id=s.id, scene_id=s.scene_id, reason="no approved take"))
        elif s.id not in clipped:
            missing.append(ReelMissingOut(shot_id=s.id, scene_id=s.scene_id, reason="not synced yet"))

    plans = []
    for scene in scenes:
        cps = []
        for c in by_scene.get(scene.id, []):
            g = takes.get(c.shot_id)
            if g is None or g.id != c.generation_id or not c.enabled:
                continue
            f = generation_file(g)
            if f is None or not f.exists():
                missing.append(ReelMissingOut(shot_id=c.shot_id, scene_id=scene.id, reason="take file missing"))
                continue
            src = c.source_duration_s or float((g.params or {}).get("duration_s") or 4.0)
            cps.append(ClipPlan(c, g, src, clip_duration(src, c.trim_in_s, c.trim_out_s)))
        length = sum(cp.duration for cp in cps)
        for prev, cur in zip(cps, cps[1:]):
            length -= seam_length(cur.clip.transition_in, cur.clip.transition_s, prev.duration, cur.duration)
        h = scene_hash(cps, size)
        m = mezz.get(scene.id)
        f = generation_file(m) if m else None
        fresh = bool(cps and m and (m.params or {}).get("input_hash") == h and f is not None and f.exists())
        plans.append(ScenePlan(scene, cps, h, round(length, 3), m, fresh))
    return plans, missing


def film_length(plans: list[ScenePlan]) -> float:
    live = [p for p in plans if p.clips]
    total = sum(p.length for p in live)
    for prev, cur in zip(live, live[1:]):
        total -= seam_length(cur.head.transition_in, cur.head.transition_s, prev.length, cur.length)
    return round(total, 3)


FULL_FILM = "Full film"


def range_label(numbers: list[int]) -> str:
    """'Scene 3', 'Scenes 2–5' for a contiguous run, 'Scenes 1, 3, 6' otherwise."""
    nums = sorted(set(numbers))
    if not nums:
        return ""
    if len(nums) == 1:
        return f"Scene {nums[0]}"
    if nums[-1] - nums[0] == len(nums) - 1:
        return f"Scenes {nums[0]}–{nums[-1]}"
    return "Scenes " + ", ".join(str(n) for n in nums)


@dataclass
class Selection:
    plans: list[ScenePlan]  # included scenes, film order, each with at least one playable clip
    numbers: list[int]      # 1-based positions in the film
    full: bool
    empty: list[tuple[int, Scene]]  # asked-for scenes with nothing to play

    @property
    def scene_ids(self) -> list[str]:
        return [p.scene.id for p in self.plans]

    @property
    def label(self) -> str:
        return range_label(self.numbers)

    @property
    def key(self) -> str:
        # what makes two stitch requests "the same job"
        return "all" if self.full else ",".join(self.scene_ids)

    @property
    def auto_title(self) -> str:
        return FULL_FILM if self.full else self.label


def select_scenes(plans: list[ScenePlan], scene_ids: list[str] | None) -> Selection:
    pos = {p.scene.id: i for i, p in enumerate(plans, start=1)}
    live = {p.scene.id for p in plans if p.clips}
    wanted = set(scene_ids) if scene_ids else live
    chosen = [p for p in plans if p.scene.id in wanted]
    included = [p for p in chosen if p.clips]
    empty = [(pos[p.scene.id], p.scene) for p in chosen if not p.clips]
    full = bool(included) and {p.scene.id for p in included} == live
    return Selection(included, [pos[p.scene.id] for p in included], full, empty)


def active_job(db: Session, project_id: str, key: str | None = None) -> Job | None:
    """The oldest queued/running assembly; with `key`, only one stitching that exact selection."""
    jobs = db.scalars(select(Job).where(
        Job.project_id == project_id, Job.type == ASSEMBLE_JOB, Job.status.in_(("queued", "running")),
    ).order_by(Job.created_at)).all()
    if key is None:
        return jobs[0] if jobs else None
    # jobs queued before range stitching existed always meant the whole film
    return next((j for j in jobs if (j.payload or {}).get("selection", "all") == key), None)


def renders(db: Session, project_id: str) -> list[Generation]:
    return list(db.scalars(select(Generation).where(
        Generation.project_id == project_id, Generation.target_type == "project", Generation.kind == "render",
    ).order_by(Generation.created_at.desc())).all())


def render_out(g: Generation) -> RenderOut:
    p = g.params or {}
    out = RenderOut(**gen_out(g).model_dump())
    # renders made before range stitching were always the whole film
    out.title = p.get("title") or FULL_FILM
    out.scene_ids = list(p.get("scene_ids") or [m.get("scene_id") for m in p.get("mezzanines", [])])
    out.scene_range = p.get("scene_range") or ""
    out.full = bool(p.get("full", True))
    out.duration_s = p.get("duration_s")
    out.clips = p.get("clips")
    out.approved = g.status == "approved"
    return out


_UNSAFE = re.compile(r"[^A-Za-z0-9]+")


def safe_name(text: str, fallback: str = "film") -> str:
    # drop accents but keep separators like the en dash in "Scenes 2–5" as word breaks
    ascii_ = "".join(ch if ch.isascii() else ("" if unicodedata.combining(ch) else "-")
                     for ch in unicodedata.normalize("NFKD", text))
    return _UNSAFE.sub("-", ascii_).strip("-")[:80].strip("-") or fallback


def download_name(project: Project | None, g: Generation) -> str:
    ext = Path(g.file_path or "").suffix or ".mp4"
    what = (g.params or {}).get("title") or (FULL_FILM if g.kind == "render" else g.kind)
    proj = safe_name(project.title, "project") if project else "project"
    return f"{proj}-{safe_name(what)}-v{g.version}{ext}"


def _thumbs(db: Session, gens: list[Generation]) -> dict[str, str | None]:
    ids = {g.id: (g.params or {}).get("first_frame_id") for g in gens}
    want = [i for i in ids.values() if i]
    files = dict(db.execute(select(Generation.id, Generation.file_path).where(Generation.id.in_(want))).all()) if want else {}
    return {gid: media_url(files.get(fid)) if fid else None for gid, fid in ids.items()}


def reel_out(db: Session, reel: Reel) -> ReelOut:
    plans, missing = plan(db, reel)
    building = active_job(db, reel.project_id) is not None
    # disabled clips still belong in the edit view, so list every synced clip, not only the playable ones
    takes = _approved_takes(db, reel.project_id)
    all_clips = [c for c in _clips(db, reel.id) if (g := takes.get(c.shot_id)) is not None and g.id == c.generation_id]
    thumbs = _thumbs(db, [takes[c.shot_id] for c in all_clips])

    scenes = []
    for p in plans:
        if p.fresh:
            status = "fresh"
        elif not p.clips:
            status = "missing"
        elif building:
            status = "building"
        else:
            status = "stale" if p.mezzanine else "missing"
        clips = []
        for c in (c for c in all_clips if c.scene_id == p.scene.id):
            g = takes[c.shot_id]
            src = c.source_duration_s or float((g.params or {}).get("duration_s") or 4.0)
            clips.append(ReelClipOut(
                id=c.id, shot_id=c.shot_id, scene_id=c.scene_id, order=c.order, generation_id=c.generation_id,
                media_url=media_url(g.file_path), thumb_url=thumbs.get(g.id), source_duration_s=src,
                trim_in_s=c.trim_in_s, trim_out_s=c.trim_out_s, duration_s=clip_duration(src, c.trim_in_s, c.trim_out_s),
                transition_in=c.transition_in, transition_s=c.transition_s, enabled=c.enabled, changed=c.changed,
            ))
        scenes.append(ReelSceneOut(
            scene_id=p.scene.id, heading=p.scene.heading, order=p.scene.order, duration_s=p.length,
            mezzanine=MezzanineOut(status=status, generation_id=p.mezzanine.id if p.mezzanine else None),
            clips=clips,
        ))
    # the Reel's "last render" is the newest whole-film stitch; partial ones live in Output
    last = next((g for g in renders(db, reel.project_id)
                 if g.status in ("ready", "approved") and (g.params or {}).get("full", True)), None)
    return ReelOut(id=reel.id, project_id=reel.project_id, duration_s=film_length(plans), scenes=scenes,
                   missing=missing, last_render=gen_out(last) if last else None)


def estimate(db: Session, reel: Reel) -> dict:
    plans, _ = plan(db, reel)
    live = [p for p in plans if p.clips]
    stale = [p for p in live if not p.fresh]
    # TODO: calibrate against wall_seconds of past renders instead of these rough encode ratios
    est = 3.0 + sum(p.length * 1.2 + len(p.clips) * 0.8 for p in stale) + film_length(plans) * 0.15
    return {"clips": sum(len(p.clips) for p in live), "duration_s": film_length(plans),
            "stale_scenes": len(stale), "est_seconds": round(est, 1)}


def reel_events(db: Session, workspace_id: str, since: datetime, jobs: list[Job]) -> list[tuple[str, str, datetime, dict]]:
    """SSE rows: a Reel whenever it, one of its clips, or an assembly job's status moved."""
    stamps: dict[str, datetime] = {}
    for r in db.scalars(select(Reel).where(Reel.workspace_id == workspace_id, Reel.updated_at > since)).all():
        stamps[r.project_id] = r.updated_at
    for c in db.scalars(select(ReelClip).where(ReelClip.workspace_id == workspace_id, ReelClip.updated_at > since)).all():
        r = db.get(Reel, c.reel_id)
        if r:
            stamps[r.project_id] = max(stamps.get(r.project_id, c.updated_at), c.updated_at)
    for j in jobs:
        if j.type != ASSEMBLE_JOB or not j.project_id:
            continue
        # progress ticks bump updated_at constantly; only queue/start/finish change what the Reel shows
        moved = [t for t in (j.created_at, j.started_at, j.finished_at) if t and t > since]
        if moved:
            stamps[j.project_id] = max(stamps.get(j.project_id, moved[0]), *moved)
    out = []
    for project_id, stamp in stamps.items():
        project = db.get(Project, project_id)
        reel = db.scalars(select(Reel).where(Reel.project_id == project_id)).first() if project else None
        if reel:
            out.append(("reel", reel.id, stamp, reel_out(db, reel).model_dump(mode="json")))
    return out


# ---------------------------------------------------------------- assembly

def _gen_path(g: Generation) -> tuple[Path, str]:
    rel = Path("workspaces") / g.workspace_id / "projects" / (g.project_id or "_unassigned") / "generations" / f"{g.id}.mp4"
    return get_settings().data_dir / rel, rel.as_posix()


def _check(cb) -> None:
    if cb:
        cb()


def build_mezzanine(cp_list: list[ClipPlan], size: tuple[int, int], workdir: Path, dst: Path,
                    check=None) -> dict:
    """Normalise each clip, then join them with their transitions. Falls back to hard cuts."""
    workdir.mkdir(parents=True, exist_ok=True)
    inputs, items = [], []
    for i, cp in enumerate(cp_list):
        _check(check)
        src = generation_file(cp.gen)
        out = workdir / f"n{i:03d}.mp4"
        run_ff(normalise_cmd(src, out, size=size, trim_in=cp.clip.trim_in_s, duration=cp.duration,
                             has_audio=probe(src).has_audio))
        inputs.append(out)
        items.append(Item(cp.duration, cp.clip.transition_in, cp.clip.transition_s))
    _check(check)
    fallback = None
    args, length = mezzanine_cmd(inputs, items, dst)
    try:
        run_ff(args)
    except FFmpegError as e:
        if all(it.transition == "cut" for it in items[1:]):
            raise
        log.warning("scene transitions failed, falling back to hard cuts: %s", e)
        fallback = "hard_cut"
        args, length = mezzanine_cmd(inputs, items, dst, hard_cuts=True)
        run_ff(args)
    return {"length": round(length, 3), "fallback": fallback}


@dataclass
class Mezz:
    path: Path
    length: float
    heading: str
    transition: str = "cut"
    transition_s: float = 0.5


def join_film(mezz: list[Mezz], workdir: Path, dst: Path, check=None) -> dict:
    """Concat mezzanines (stream copy) with seam clips at scene boundaries; returns timeline + fallbacks."""
    workdir.mkdir(parents=True, exist_ok=True)
    fallbacks: list[str] = []
    seams: dict[int, tuple[str, float]] = {}
    for k in range(1, len(mezz)):
        kind = mezz[k].transition
        if kind not in ("dissolve", "fade_black"):
            continue
        d = frames(clamp_transition(mezz[k].transition_s, mezz[k - 1].length, mezz[k].length))
        if kind == "fade_black":
            d = 2 * frames(d / 2)
        seams[k] = (kind, d)

    def eaten(seam: tuple[str, float] | None) -> float:
        # how much of each neighbouring mezzanine the seam clip replaces
        if seam is None:
            return 0.0
        return seam[1] if seam[0] == "dissolve" else seam[1] / 2

    entries: list[Entry] = []
    starts: list[float] = []
    t = 0.0
    for k, m in enumerate(mezz):
        _check(check)
        starts.append(t)  # a scene's chapter opens where its incoming seam begins
        if k in seams:
            kind, d = seams[k]
            seam = workdir / f"seam{k:03d}.mp4"
            try:
                run_ff(seam_cmd(kind, mezz[k - 1].path, mezz[k - 1].length, m.path, d, seam))
                entries.append(Entry(seam))
                t += d
            except FFmpegError as e:
                log.warning("seam %d (%s) failed, using a hard cut: %s", k, kind, e)
                fallbacks.append(f"seam {k}: hard cut")
                # the previous mezzanine was cut short for this seam; give its tail back
                t += eaten(seams.pop(k))
                entries[-1].outpoint = None
                starts[-1] = t
        inpoint = eaten(seams.get(k)) or None
        tail = eaten(seams.get(k + 1))
        outpoint = m.length - tail if tail else None
        entries.append(Entry(m.path, inpoint, outpoint))
        t += m.length - (inpoint or 0.0) - tail
    ends = starts[1:] + [t]
    chapters = [(m.heading, s, e) for m, s, e in zip(mezz, starts, ends)]

    lst = write_concat_list(workdir, entries)
    _check(check)
    mode = "copy"
    try:
        run_ff(concat_copy_cmd(lst, dst))
        got = probe(dst).duration
        # a copy join that silently mangled timestamps shows up as a length mismatch
        if abs(got - t) > max(0.3, 0.05 * len(entries)):
            raise FFmpegError(f"copy join came out {got:.2f}s, expected {t:.2f}s")
    except FFmpegError as e:
        log.warning("stream-copy join failed, re-encoding: %s", e)
        fallbacks.append("join: re-encode")
        mode = "reencode"
        run_ff(concat_reencode_cmd(lst, dst))
    return {"length": round(t, 3), "chapters": chapters, "fallbacks": fallbacks, "mode": mode, "list": str(lst)}


def finish_film(src: Path, workdir: Path, dst: Path, title: str, chapters) -> dict:
    stats = measure_loudness(src)
    meta = write_chapters(workdir, title, chapters)
    run_ff(finish_cmd(src, meta, dst, stats))
    return {"loudnorm": bool(stats), "input_i": float(stats["input_i"]) if stats else None, "target_i": LUFS}


def _workdir(job_id: str) -> Path:
    return job_workdir("reel", job_id)


def handle_assemble(ctx) -> dict:
    db, job = ctx.db, ctx.job
    t0 = time.monotonic()
    project = db.get(Project, job.project_id) if job.project_id else None
    render = db.get(Generation, job.generation_id) if job.generation_id else None
    if project is None or render is None:
        raise RuntimeError("The project or its render record no longer exists")

    reel = get_reel(db, project)
    sync(db, reel)
    db.commit()
    plans, _ = plan(db, reel)
    sel = select_scenes(plans, (job.payload or {}).get("scene_ids") or None)
    live = sel.plans
    if not live:
        raise RuntimeError("Nothing to stitch: the selected scenes have no approved takes")
    size = reel_size(db, reel)
    asked = render.params or {}
    # takes may have come or gone since the request; an automatic name follows what's really in it
    title = sel.auto_title if asked.get("title_auto", True) or not asked.get("title") else asked["title"]

    render.status = "generating"
    reel.updated_at = utcnow()
    db.commit()

    work = _workdir(job.id)
    check = lambda: ctx.progress(job.progress)  # noqa: E731  (raises when cancelled)
    built, cached, fallbacks = [], [], []
    try:
        n = len(live)
        for i, p in enumerate(live, start=1):
            if p.fresh:
                cached.append(p.scene.id)
                continue
            ctx.progress(0.05 + 0.65 * (i - 1) / n, f"Scene {i} of {n} · building")
            tmp_out = work / f"mezz{i:03d}.mp4"
            info = build_mezzanine(p.clips, size, work / f"s{i:03d}", tmp_out, check)
            # the row only appears once the file exists, so a cancel never leaves a half-built mezzanine
            g = Generation(id=new_id(), workspace_id=project.workspace_id, project_id=project.id,
                           target_type="scene", target_id=p.scene.id, kind="mezzanine",
                           version=next_version(db, "scene", p.scene.id, "mezzanine"),
                           status="ready", prompt="", seed=0, job_id=job.id, media_type="video/mp4")
            abs_path, rel = _gen_path(g)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_out), abs_path)
            g.file_path = rel
            db.add(g)
            g.params = {
                "input_hash": p.hash, "pipeline": PIPELINE, "size": list(size), "duration_s": info["length"],
                "fallback": info["fallback"],
                "clips": [{"clip_id": cp.clip.id, "shot_id": cp.clip.shot_id, "generation_id": cp.gen.id,
                           "trim_in_s": cp.clip.trim_in_s, "trim_out_s": cp.clip.trim_out_s,
                           "transition_in": cp.clip.transition_in, "transition_s": cp.clip.transition_s,
                           "duration_s": cp.duration} for cp in p.clips],
            }
            if info["fallback"]:
                fallbacks.append(f"scene {i}: {info['fallback']}")
            for cp in p.clips:
                cp.clip.changed = False
            p.mezzanine, p.fresh = g, True
            reel.updated_at = utcnow()
            built.append(p.scene.id)
            db.commit()

        mezz = []
        intro = title_intro.for_render(project, sel.full)
        if intro:
            # contract v4a: the opening title goes in front as one more piece of the join
            ctx.progress(0.72, "Opening title")
            got = title_intro.build(db, intro, live[0].scene, live[0].clips[0].gen, size, work / "intro",
                                    work / "intro.mp4")
            intro.update(got)
            mezz.append(Mezz(work / "intro.mp4", got["length"], title_intro.CHAPTER))
        ctx.progress(0.75, "Joining film")
        for p in live:
            m = p.mezzanine
            length = float((m.params or {}).get("duration_s") or p.length)
            mezz.append(Mezz(generation_file(m), length, p.scene.heading or f"Scene {p.scene.order}",
                             p.head.transition_in, p.head.transition_s))
        joined = work / "joined.mp4"
        j = join_film(mezz, work / "film", joined, check)
        fallbacks += j["fallbacks"]

        ctx.progress(0.88, "Loudness pass")
        final_tmp = work / "final.mp4"
        loud = finish_film(joined, work, final_tmp, f"{project.title} - {title}", j["chapters"])

        abs_path, rel = _gen_path(render)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final_tmp), abs_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    wall = round(time.monotonic() - t0, 3)
    film_hash = hashlib.sha256(json.dumps(
        [[p.mezzanine.params.get("input_hash"), p.head.transition_in, round(p.head.transition_s, 3)] for p in live]
    ).encode()).hexdigest()[:32]
    db.refresh(render)
    if render.params.get("title_auto") is False:
        title = render.params.get("title") or title  # renamed while it was stitching
    render.file_path, render.media_type, render.status = rel, "video/mp4", "ready"
    render.params = {
        **(render.params or {}), "input_hash": film_hash,
        "quality": "hq" if (project.settings or {}).get("video_quality") == "hq" else "draft", "size": list(size),
        "title": title, "scene_ids": sel.scene_ids, "scene_range": sel.label, "full": sel.full,
        "clips": sum(len(p.clips) for p in live),
        "duration_s": j["length"], "join": j["mode"], "fallbacks": fallbacks, "loudness": loud,
        "mezzanines": [{"scene_id": p.scene.id, "generation_id": p.mezzanine.id,
                        "input_hash": p.mezzanine.params.get("input_hash")} for p in live],
        "chapters": [{"title": c[0], "start_s": round(c[1], 3), "end_s": round(c[2], 3)} for c in j["chapters"]],
    }
    if intro:
        render.params["title_intro"] = intro
    reel.updated_at = utcnow()
    return {"file_path": rel, "media_type": "video/mp4", "duration_s": j["length"], "wall_seconds": wall,
            "scenes_built": built, "scenes_cached": cached, "fallbacks": fallbacks}

