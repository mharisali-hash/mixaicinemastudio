"""AI video podcast (contract v24): the record, its hidden project (hosts as Characters, the set as a Location,
the angle library and one Shot per camera track), the tracks' audio and signatures, and the shapes the API
returns. The stage machine that fills it in is app.podcast.video_stages."""
import hashlib
import json
import logging
import math
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import audio_analysis as aa
from app import character_looks as looks
from app import character_sheet as cs
from app import dialogue as dl
from app import estimate as est
from app import library as lib
from app import longtake
from app import reel as rl
from app import storyboard as sb
from app.config import get_settings
from app.drivers.comfy import HQ_SIZES, MAX_FRAMES, VIDEO_SIZES
from app.models import (Character, Generation, Job, Location, MediaItem, PodcastEpisode, Project, Scene, Shot,
                        VideoPodcast, new_id, utcnow)
from app.podcast import service as sv
from app.podcast import vplan
from app.services import (approve, enqueue_generation, gen_out, generation_file, job_out, media_url, new_seed,
                          next_version)
from app.workflows import frames_for

log = logging.getLogger("mixai.video_podcast")

JOB = "video_podcast"
ASSEMBLE_JOB = "video_podcast_assemble"
STAGES = (("cast", "Hosts"), ("set", "Set"), ("keyframes", "Look"), ("plan", "Cameras & cut"), ("render", "Render"),
          ("assemble", "Assemble"))
CONTINUE_STAGES = ("cast", "set", "keyframes", "plan")
QUALITIES = ("draft", "final")
ASPECTS = ("16:9", "9:16", "1:1")
FINISHED = ("ready", "approved")
LIVE = ("queued", "generating")
CONSENT = "I have the right to use this person's likeness, and they agreed to it being animated."

# the hidden project's style bible rides on every angle frame
STYLE = ("Photorealistic podcast studio footage, natural skin texture, soft key light with a warm rim light, "
         "shallow depth of field, true-to-life colour.")
SET_PRESETS = {
    "modern_studio": ("Modern studio", "a modern podcast studio with acoustic foam panels, warm LED strip lights, "
                                       "a round wooden table and boom-arm microphones"),
    "cosy_room": ("Cosy room", "a cosy living-room podcast set with bookshelves, warm table lamps, armchairs, a rug "
                               "and plants, microphones on stands"),
    "news_desk": ("News desk", "a television news desk set with a long glossy desk, a city skyline screen behind, "
                               "bright even studio lighting and desk microphones"),
    "rooftop": ("Rooftop", "a rooftop podcast set at golden hour with the city skyline behind, string lights, "
                           "lounge chairs and microphones on stands"),
    "desi_baithak": ("Desi baithak", "a traditional South Asian baithak with low floor seating, gao takiya bolsters, "
                                     "a hand-woven rug, a carved wooden jharokha, brass lamps and warm light, "
                                     "microphones on low stands"),
}
DEFAULT_SETTINGS = {"aspect": "16:9", "style": "realistic studio", "subtitles": "burn", "caption_style": "classic", "set_preset": "modern_studio",
                    "set_prompt": "", "cover_intro": False, "cover_outro": False, "cover_media_id": None}
ANGLE_LABELS = {"close": "Close-up", "three_quarter": "3/4 close-up", "listen": "Listening", "wide": "Wide",
                "medium": "Two-shot"}
# rough extra GPU time per piece on top of the per-second rate: text encode, audio encode, VAE decode
TAKE_OVERHEAD_S = 8.0
IA2V_FACTOR = 1.15
QUALITY_FACTOR = {"draft": 1.0, "final": 2.5}  # 720p and a second pass against the 480p single pass
PIECE_S = 10.0  # about the most LTX-2.3 does at 720p in one pass on the 20 GB cards
SEGMENT_MAX_S = vplan.SEGMENT_MAX_S
GUIDE_STRENGTH = 0.2  # enough to hold the face over a long take without freezing the pose


class VideoPodcastError(RuntimeError):
    pass


# ---------------------------------------------------------------- record basics

def settings_of(vp: VideoPodcast) -> dict:
    return {**DEFAULT_SETTINGS, **(vp.settings or {})}


def initial_stages() -> dict:
    return {k: {"status": "pending"} for k, _ in STAGES}


def stage(vp: VideoPodcast, key: str) -> dict:
    return dict((vp.stages or {}).get(key) or {"status": "pending"})


def put_stage(vp: VideoPodcast, key: str, rec: dict) -> None:
    vp.stages = {**(vp.stages or {}), key: rec}
    flag_modified(vp, "stages")


def refs(vp: VideoPodcast) -> dict:
    return dict((vp.stages or {}).get("refs") or {})


def put_refs(vp: VideoPodcast, rec: dict) -> None:
    vp.stages = {**(vp.stages or {}), "refs": rec}
    flag_modified(vp, "stages")


def put_host(vp: VideoPodcast, host_id: str, entry: dict) -> None:
    vp.hosts = {**(vp.hosts or {}), host_id: entry}
    flag_modified(vp, "hosts")


def reset_from(vp: VideoPodcast, key: str) -> None:
    """Back to `key`: it and every later stage are pending again (their generations stay)."""
    keys = [k for k, _ in STAGES]
    for k in keys[keys.index(key):]:
        rec = stage(vp, k)
        rec.update(status="pending", detail=None)
        put_stage(vp, k, rec)
    if vp.stage not in keys or keys.index(vp.stage) > keys.index(key):
        vp.stage = key


def busy(db: Session, vp: VideoPodcast) -> Job | None:
    j = db.get(Job, vp.job_id) if vp.job_id else None
    return j if j is not None and j.status in ("queued", "running") else None


def ensure_idle(db: Session, vp: VideoPodcast) -> None:
    if busy(db, vp) is not None:
        raise HTTPException(409, "The AI video is working; wait for it, or cancel the job")


def episode_of(db: Session, vp: VideoPodcast) -> PodcastEpisode:
    ep = db.get(PodcastEpisode, vp.episode_id)
    if ep is None:
        raise VideoPodcastError("The episode for this AI video no longer exists")
    return ep


def check_consent(consent: dict | None, user_id: str) -> dict:
    c = consent or {}
    name = " ".join(str(c.get("name") or "").split())[:120]
    if not name:
        raise HTTPException(422, "Using a photo of a real person needs a consent record: the person's name")
    if " ".join(str(c.get("statement") or "").split()) != CONSENT:
        raise HTTPException(422, f"The consent statement must read exactly: “{CONSENT}”")
    return {"name": name, "statement": CONSENT, "at": utcnow().isoformat() + "Z", "user_id": user_id}


# ---------------------------------------------------------------- creating it

def find(db: Session, ep: PodcastEpisode) -> VideoPodcast | None:
    return db.scalars(select(VideoPodcast).where(VideoPodcast.episode_id == ep.id)).first()


def create(db: Session, ep: PodcastEpisode, aspect: str | None) -> tuple[VideoPodcast, bool]:
    vp = find(db, ep)
    if vp is not None:
        return vp, False
    if not ep.hosts:
        raise HTTPException(409, "Add the episode's hosts first")
    aspect = aspect if aspect in ASPECTS else "16:9"
    mix = (ep.outputs or {}).get("mix") or {}
    project = Project(workspace_id=ep.workspace_id, title=f"{ep.title} · AI video"[:300], authoring_mode="podcast",
                      aspect_ratio=aspect, style_bible=STYLE, brief="",
                      target_runtime_s=max(1, int(math.ceil(mix.get("duration_s") or 60))), quality="draft",
                      takes_per_shot=1, settings={"podcast_episode_id": ep.id})
    db.add(project)
    db.flush()
    vp = VideoPodcast(workspace_id=ep.workspace_id, episode_id=ep.id, project_id=project.id, stage="cast",
                      stages=initial_stages(), settings={**DEFAULT_SETTINGS, "aspect": aspect}, hosts={}, plan=[],
                      outputs={})
    db.add(vp)
    db.flush()
    sync_hosts(db, vp, ep)
    apply_linked_hosts(db, vp, ep)
    return vp, True


def sync_hosts(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> None:
    """One Character per episode host; a host added to the episode later gets one, a removed host loses its."""
    cur = dict(vp.hosts or {})
    wanted = {h["id"]: h for h in ep.hosts or []}
    for hid, h in wanted.items():
        entry = cur.get(hid)
        c = db.get(Character, entry["character_id"]) if entry else None
        if c is None:
            c = Character(workspace_id=vp.workspace_id, project_id=vp.project_id, name=h["name"][:200],
                          description="", source="podcast")
            db.add(c)
            db.flush()
            cur[hid] = {"character_id": c.id, "source": "persona", "rev": (entry or {}).get("rev", 0) + 1}
        elif c.name != h["name"]:
            c.name = h["name"][:200]
    for hid in [k for k in cur if k not in wanted]:
        del cur[hid]
    if cur != (vp.hosts or {}):
        vp.hosts = cur
        flag_modified(vp, "hosts")


def hosts_in_order(vp: VideoPodcast, ep: PodcastEpisode) -> list[dict]:
    return [h for h in ep.hosts or [] if h["id"] in (vp.hosts or {})]


# ---------------------------------------------------------------- hosts

def portraits(db: Session, character_id: str) -> list[Generation]:
    return list(db.scalars(select(Generation).where(
        Generation.target_type == "character", Generation.target_id == character_id, Generation.kind == "portrait",
    ).order_by(Generation.created_at.desc())).all())


def fresh(gens: list[Generation], key: str, value) -> list[Generation]:
    return [g for g in gens if (g.params or {}).get(key) == value]


def pick(gens: list[Generation], show: bool = False) -> Generation | None:
    """Newest first in: the approved one, else the newest. `show` (the page) puts a newer one that's still
    being made or waiting for its Approve ahead of it; with the approved one shown instead, a regenerated
    angle never appeared and couldn't be approved (24 Oct)."""
    top = next(iter(gens), None)
    if show and top is not None and (top.status in LIVE or top.status == "ready"):
        return top
    return next((g for g in gens if g.status == "approved"), None) or top


def current_portrait(db: Session, entry: dict, show: bool = False) -> Generation | None:
    """The approved portrait of this host choice, else its newest one (any status)."""
    return pick(fresh(portraits(db, entry["character_id"]), "vp_rev", entry.get("rev")), show)


def usable_portrait(db: Session, entry: dict) -> Generation | None:
    gens = fresh(portraits(db, entry["character_id"]), "vp_rev", entry.get("rev"))
    return next((g for g in gens if g.status == "approved"), None) or \
        next((g for g in gens if g.status == "ready"), None)


def _copy_file(g: Generation, project_id: str, gen_id: str) -> str:
    src = generation_file(g)
    if src is None or not src.is_file():
        raise HTTPException(409, "That character's portrait file is missing")
    rel = Path("workspaces") / g.workspace_id / "projects" / project_id / "generations" / f"{gen_id}{src.suffix}"
    dst = get_settings().data_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return rel.as_posix()


def choose_host(db: Session, cur, vp: VideoPodcast, ep: PodcastEpisode, host_id: str, choice: dict) -> None:
    entry = dict((vp.hosts or {}).get(host_id) or {})
    if not entry:
        raise HTTPException(422, f"This episode has no host '{host_id}'")
    c = db.get(Character, entry["character_id"])
    host = next(h for h in ep.hosts if h["id"] == host_id)
    src = choice.get("source")
    rev = int(entry.get("rev") or 0) + 1
    new = {"character_id": c.id, "source": src, "rev": rev}
    if src == "persona":
        c.description = ""
    elif src == "photo":
        item = db.get(MediaItem, choice.get("media_id") or "")
        if item is None or item.workspace_id != vp.workspace_id:
            raise HTTPException(404, "That photo isn't in the Library")
        if item.kind != "image":
            raise HTTPException(422, "The photo has to be an image")
        g = db.get(Generation, item.generation_id or "")
        if g is None or g.status not in FINISHED or not g.file_path:
            raise HTTPException(409, "That photo isn't finished yet")
        # consent first, as for a voice clone: nothing is made from a real person without it
        new.update(photo_media_id=item.id, photo_generation_id=g.id,
                   consent=check_consent(choice.get("consent"), cur.id))
        c.description = c.description or f"{host['name']}, from the uploaded photo"
        if choice.get("as_is"):
            # the photo itself is the portrait, approved now, so the cast stage only turns it for the views
            cs.add_own(db, c, cs.open_picture(g), params={"from_photo": item.id, "vp": vp.id, "vp_rev": rev,
                                                          "consent": new["consent"]})
            new["as_is"] = True
    elif src in ("existing", "studio"):
        other = db.get(Character, choice.get("character_id") or "")
        if other is None or other.workspace_id != vp.workspace_id:
            raise HTTPException(404, "That character doesn't exist")
        if other.project_id == vp.project_id:
            raise HTTPException(422, "Pick a character from another project")
        og = None
        if src == "studio":
            if other.project_id is not None:
                raise HTTPException(422, "Pick a character from Character Studio")
            if choice.get("look_id"):
                og = looks.finished_picture(db, other, choice["look_id"])
                if og is None:
                    raise HTTPException(409, f"That look of {other.name} has no finished picture yet")
                new["look_id"] = choice["look_id"]
        og = og or sb.approved(db, "character", other.id, "portrait") or next(
            (g for g in portraits(db, other.id) if g.status == "ready" and g.file_path), None)
        if og is None:
            raise HTTPException(409, f"{other.name} has no portrait yet")
        c.description = other.description
        gid = new_id()
        copy = Generation(id=gid, workspace_id=vp.workspace_id, project_id=vp.project_id, target_type="character",
                          target_id=c.id, kind="portrait", version=next_version(db, "character", c.id, "portrait"),
                          status="ready", prompt=og.prompt, seed=og.seed, media_type=og.media_type,
                          file_path=_copy_file(og, vp.project_id, gid),
                          params={**(og.params or {}), "copied_from": og.id, "vp": vp.id, "vp_rev": rev})
        db.add(copy)
        db.flush()
        approve(db, copy)
        new["from_character_id"] = other.id
    else:
        raise HTTPException(422, "A host is made from its persona, a photo or an existing character")
    put_host(vp, host_id, new)


def apply_linked_hosts(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> bool:
    """Episode hosts that are Character Studio characters take that character's look as their portrait. A host
    given a photo by hand in the video's Hosts step keeps it. True when a portrait changed (cast needs redoing)."""
    changed = False
    for h in ep.hosts or []:
        entry = (vp.hosts or {}).get(h["id"])
        if not entry or entry.get("source") == "photo":
            continue
        cid, lid = h.get("character_id"), h.get("look_id")
        if not cid:
            if entry.get("source") == "studio":
                # unlinked in the episode: back to a face from the persona
                choose_host(db, None, vp, ep, h["id"], {"source": "persona"})
                changed = True
            continue
        if entry.get("source") == "studio" and entry.get("from_character_id") == cid and entry.get("look_id") == lid:
            continue
        try:
            choose_host(db, None, vp, ep, h["id"], {"source": "studio", "character_id": cid, "look_id": lid})
        except HTTPException as e:
            # a look still being drawn, or a character with no portrait yet: the next continue tries again
            log.info("host %s not linked yet: %s", h["id"], e.detail)
            continue
        changed = True
    return changed


# ---------------------------------------------------------------- the set and the angle library

def set_description(vp: VideoPodcast) -> str:
    st = settings_of(vp)
    base = SET_PRESETS.get(st["set_preset"], SET_PRESETS["modern_studio"])[1]
    extra = " ".join(str(st.get("set_prompt") or "").split())
    return f"{base}. {extra}".strip() if extra else base


def set_prompt(vp: VideoPodcast, n_hosts: int) -> str:
    seats = {1: "one seat", 2: "two seats facing each other", 3: "three seats in a gentle arc"}.get(
        n_hosts, f"{n_hosts} seats in a gentle arc")
    return (f"Establishing wide shot of {set_description(vp)}. {seats.capitalize()}, each with its own microphone, "
            "set up for a conversation; nobody in the shot. Photorealistic, eye-level camera, clean composition.")


def set_key(vp: VideoPodcast, n_hosts: int) -> str:
    st = settings_of(vp)
    return hashlib.sha1(json.dumps([set_prompt(vp, n_hosts), st["aspect"]]).encode()).hexdigest()[:16]


def ensure_location(db: Session, vp: VideoPodcast) -> Location:
    loc = db.get(Location, vp.set_location_id or "")
    st = settings_of(vp)
    name = SET_PRESETS.get(st["set_preset"], SET_PRESETS["modern_studio"])[0]
    if loc is None:
        loc = Location(workspace_id=vp.workspace_id, project_id=vp.project_id, name=name, source="podcast",
                       description=set_description(vp))
        db.add(loc)
        db.flush()
        vp.set_location_id = loc.id
    else:
        loc.name, loc.description = name, set_description(vp)
    return loc


def establishing(db: Session, vp: VideoPodcast, fresh_only: bool = True, show: bool = False) -> Generation | None:
    if not vp.set_location_id:
        return None
    gens = list(db.scalars(select(Generation).where(
        Generation.target_type == "location", Generation.target_id == vp.set_location_id,
        Generation.kind == "establishing").order_by(Generation.created_at.desc())).all())
    if fresh_only:
        ep_hosts = len(vp.hosts or {})
        gens = fresh(gens, "vp_set", set_key(vp, ep_hosts))
    return pick(gens, show)


def angle_keys(vp: VideoPodcast, ep: PodcastEpisode) -> list[tuple[str, str | None, str]]:
    """(key, host_id, angle) for the whole library, hosts in episode order then the shared shots."""
    out = []
    for h in hosts_in_order(vp, ep):
        out += [(vplan.angle_key(h["id"], a), h["id"], a) for a in vplan.HOST_ANGLES]
    out += [(a, None, a) for a in vplan.SHARED_ANGLES]
    return out


def ensure_scenes(db: Session, vp: VideoPodcast) -> dict:
    r = refs(vp)
    for key, heading, order in (("angles_scene", "ANGLE LIBRARY", 1), ("episode_scene", "EPISODE", 2)):
        if not r.get(key) or db.get(Scene, r[key]) is None:
            sc = Scene(workspace_id=vp.workspace_id, project_id=vp.project_id, order=order, heading=heading,
                       source="podcast", locked=True)
            db.add(sc)
            db.flush()
            r[key] = sc.id
    put_refs(vp, r)
    return r


def host_names(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> dict[str, str]:
    return {h["id"]: h["name"] for h in hosts_in_order(vp, ep)}


def _desc(db: Session, vp: VideoPodcast, host_id: str) -> str:
    c = db.get(Character, ((vp.hosts or {}).get(host_id) or {}).get("character_id") or "")
    return " ".join((c.description if c else "").split())[:300]


def angle_prompt(db: Session, vp: VideoPodcast, ep: PodcastEpisode, host_id: str | None, angle: str) -> str:
    names = host_names(db, vp, ep)
    place = set_description(vp)
    if host_id:
        who = names.get(host_id, "The host")
        look = _desc(db, vp, host_id)
        lead = f"{who} ({look})" if look else who
        frame = {"close": "Close-up, head and shoulders, of", "three_quarter": "Three-quarter close-up of",
                 "listen": "Close-up of"}[angle]
        act = ("listening, attentive, slight nod, mouth closed, eyes just off camera" if angle == "listen" else
               "speaking into a podcast microphone, mid-sentence, natural expression, eyes just off camera")
        # "looking towards the co-host" drew a stranger's head and shoulder into the foreground, and the
        # lip-sync model then moved his mouth instead of the host's (24 Oct)
        return (f"{frame} {lead} seated alone in {place}, {act}. A single person in the frame: nobody else, no "
                "other heads or shoulders in the foreground. Shallow depth of field, the set softly blurred behind.")
    who = " and ".join(names.values()) or "the hosts"
    if angle == "wide":
        return (f"Wide shot of {place} with {who} seated at their microphones in conversation, everyone in frame, "
                "eye-level camera.")
    return (f"Medium two-shot of {who} at their microphones in {place}, talking with each other, both faces "
            "clearly visible.")


def angle_refs(db: Session, vp: VideoPodcast, ep: PodcastEpisode, host_id: str | None,
               angle: str) -> tuple[list[str], list[str]]:
    names = host_names(db, vp, ep)
    ids, labels = [], []
    hosts = [host_id] if host_id else list(names)[:2]
    for hid in hosts:
        entry = (vp.hosts or {}).get(hid) or {}
        g = usable_portrait(db, entry)
        if angle == "three_quarter":
            sheet = db.get(Generation, ((entry.get("sheets") or {}).get("three_quarter")) or "")
            if sheet is not None and sheet.status in FINISHED:
                g = sheet
        if g is not None:
            ids.append(g.id)
            labels.append(names.get(hid, "the host"))
    est_g = establishing(db, vp)
    if est_g is not None and est_g.status in FINISHED:
        ids.append(est_g.id)
        labels.append("the podcast set")
    return ids[:sb.MAX_REFS], labels[:sb.MAX_REFS]


def ensure_angle_shots(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> dict[str, Shot]:
    r = ensure_scenes(db, vp)
    have = dict(r.get("angle_shots") or {})
    out: dict[str, Shot] = {}
    chars = {hid: e["character_id"] for hid, e in (vp.hosts or {}).items()}
    for n, (key, host_id, angle) in enumerate(angle_keys(vp, ep), start=1):
        shot = db.get(Shot, have.get(key) or "")
        if shot is None:
            shot = Shot(workspace_id=vp.workspace_id, project_id=vp.project_id, scene_id=r["angles_scene"],
                        source="podcast", locked=True)
            db.add(shot)
        shot.order = n
        shot.shot_type = "wide" if angle == "wide" else ("medium" if angle == "medium" else "close_up")
        shot.description = ANGLE_LABELS[angle]
        shot.start_prompt = angle_prompt(db, vp, ep, host_id, angle)
        shot.character_ids = [chars[host_id]] if host_id else list(chars.values())
        shot.location_id = vp.set_location_id
        shot.duration_s = 4.0
        db.flush()
        out[key] = shot
    gone = [db.get(Shot, sid) for k, sid in have.items() if k not in out]
    sb.delete_shots(db, [s for s in gone if s is not None])
    put_refs(vp, {**r, "angle_shots": {k: s.id for k, s in out.items()}})
    return out


def fresh_angle_prompt(db: Session, vp: VideoPodcast, ep: PodcastEpisode, key: str, params: dict) -> tuple[str, dict]:
    """An angle's prompt and refs as the keyframes stage would make them now."""
    shots = ensure_angle_shots(db, vp, ep)
    host_id, angle = next((h, a) for k, h, a in angle_keys(vp, ep) if k == key)
    ids, labels = angle_refs(db, vp, ep, host_id, angle)
    shot = shots[key]
    params = {**params, "aspect_ratio": settings_of(vp)["aspect"], "vp_refs": angle_tag(ids, shot.start_prompt)}
    if ids:
        params.update(reference_ids=ids, reference_labels=labels)
    return sb.prepare_shot_generation(db, shot, "keyframe_start", "", params)


def angle_tag(ids: list[str], prompt: str) -> str:
    return hashlib.sha1(json.dumps([ids, prompt]).encode()).hexdigest()[:16]


def frame_of(db: Session, shot_id: str | None, show: bool = False) -> Generation | None:
    """An angle's frame: approved, else the newest one."""
    if not shot_id:
        return None
    gens = list(db.scalars(select(Generation).where(
        Generation.target_type == "shot", Generation.target_id == shot_id, Generation.kind == "keyframe_start",
    ).order_by(Generation.created_at.desc())).all())
    return pick(gens, show)


def angle_frames(db: Session, vp: VideoPodcast) -> dict[str, Generation]:
    out = {}
    for key, sid in (refs(vp).get("angle_shots") or {}).items():
        g = frame_of(db, sid)
        if g is not None and g.status in FINISHED:
            out[key] = g
    return out


# ---------------------------------------------------------------- the mix and the plan

def mix_of(ep: PodcastEpisode) -> dict | None:
    mix = (ep.outputs or {}).get("mix") or {}
    return mix if mix.get("generation_id") and mix.get("timeline") is not None else None


def line_takes(db: Session, ep: PodcastEpisode) -> dict[str, str]:
    """line id -> the take the mix was built from (the mix job's own list), else the script's current take."""
    mix = mix_of(ep) or {}
    job = db.get(Job, (((ep.outputs or {}).get("jobs") or {}).get("mix")) or "")
    if job is not None and job.generation_id == mix.get("generation_id"):
        items = (job.payload or {}).get("items") or []
        if items:
            return {it["line_id"]: it.get("take_id") for it in items if it.get("take_id")}
    from app.models import VoiceScript

    s = db.get(VoiceScript, ep.script_id or "")
    return {ln["id"]: ln.get("take_id") for ln in (dl.refresh(db, s) if s else []) if ln.get("take_id")}


def plan_of(vp: VideoPodcast) -> dict | None:
    """The continuous-camera plan, or None (nothing planned yet, or an old per-line shot list)."""
    p = vp.plan
    return p if isinstance(p, dict) and isinstance(p.get("tracks"), list) else None


def tracks_of(vp: VideoPodcast) -> list[dict]:
    return (plan_of(vp) or {}).get("tracks") or []


def cuts_of(vp: VideoPodcast) -> list[dict]:
    return (plan_of(vp) or {}).get("cuts") or []


def pieces_for(track: dict) -> list[dict]:
    if track["kind"] != "host":
        return [{"idx": 0, "frames": min(frames_for(track["duration"], 24.0), MAX_FRAMES)}]
    return longtake.plan_chunks(track["duration"], chunk_s=PIECE_S, overlap=longtake.context_frames("extend"))


def _finish(plan: dict) -> dict:
    for t in plan["tracks"]:
        t["pieces"] = len(pieces_for(t))
    return plan


def _build(vp: VideoPodcast, ep: PodcastEpisode, mix: dict, found: list[float] | None = None) -> dict:
    st = settings_of(vp)
    host_ids = [h["id"] for h in hosts_in_order(vp, ep)]
    return vplan.build(mix["timeline"], mix.get("chapters") or [], float(mix["duration_s"]), host_ids,
                       cover_intro=bool(st["cover_intro"]), cover_outro=bool(st["cover_outro"]),
                       max_s=SEGMENT_MAX_S, pauses=found, salt=vp.id)


def build_plan(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> dict:
    mix = mix_of(ep)
    if mix is None:
        raise HTTPException(409, "Mix the episode first: the video follows the mix")
    plan = _build(vp, ep, mix)
    found = pauses(db, vp, ep, plan)
    if found:
        # pauses only move cut points; the segments and tracks come out the same
        plan = _build(vp, ep, mix, found)
    return _finish(plan)


def upgrade(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> None:
    """A record planned before continuous cameras gets the new plan shape; its per-line takes don't carry over."""
    if not vp.plan or plan_of(vp) is not None:
        return
    mix = mix_of(ep)
    if mix is None:
        vp.plan = {}
        flag_modified(vp, "plan")
        return
    set_plan(db, vp, _finish(_build(vp, ep, mix)))


def set_plan(db: Session, vp: VideoPodcast, plan: dict) -> None:
    vp.plan = plan
    flag_modified(vp, "plan")
    sync_plan_shots(db, vp)


def put_cuts(vp: VideoPodcast, cuts: list[dict]) -> None:
    vp.plan = {**(plan_of(vp) or {}), "cuts": cuts}
    flag_modified(vp, "plan")


def sync_plan_shots(db: Session, vp: VideoPodcast) -> None:
    """One Shot row per camera track (same id), in the hidden project's EPISODE scene; the takes hang off it."""
    r = ensure_scenes(db, vp)
    have = {s.id: s for s in sb.scene_shots(db, r["episode_scene"])}
    chars = {hid: e["character_id"] for hid, e in (vp.hosts or {}).items()}
    for n, t in enumerate(tracks_of(vp), start=1):
        shot = have.pop(t["id"], None) or db.get(Shot, t["id"])
        if shot is None:
            shot = Shot(id=t["id"], workspace_id=vp.workspace_id, project_id=vp.project_id,
                        scene_id=r["episode_scene"], source="podcast", locked=True)
            db.add(shot)
        shot.order = n
        shot.shot_type = "close_up" if t["kind"] == "host" else "wide"
        shot.duration_s = t["duration"]
        shot.description = f"{'Host camera' if t['kind'] == 'host' else 'Wide'} {t['start']:.1f}–{t['end']:.1f} s"
        shot.character_ids = [chars[t["host_id"]]] if t.get("host_id") in chars else list(chars.values())
        shot.location_id = vp.set_location_id
    db.flush()
    sb.delete_shots(db, list(have.values()))


def cameras(vp: VideoPodcast, ep: PodcastEpisode) -> set[str]:
    return {h["id"] for h in hosts_in_order(vp, ep)} | {vplan.WIDE, vplan.COVER}


def fill_cuts(db: Session, vp: VideoPodcast, ep: PodcastEpisode, rows: list[dict]) -> list[dict]:
    """An edited cut list from the client: normalised, checked and re-linked to the mix's lines."""
    if mix_of(ep) is None or plan_of(vp) is None:
        raise HTTPException(409, "Plan the cameras first: the cut list follows the mix")
    old = {c["id"] for c in cuts_of(vp)}
    cuts = [{"id": row.get("id") if row.get("id") in old else vplan.new_id(), "camera": row.get("camera"),
             "start": vplan.snap(row["start"]), "end": vplan.snap(row["end"])}
            for row in sorted(rows, key=lambda r: float(r.get("start") or 0))]
    return checked(vp, ep, cuts)


def checked(vp: VideoPodcast, ep: PodcastEpisode, cuts: list[dict]) -> list[dict]:
    mix = mix_of(ep)
    plan = plan_of(vp) or {}
    if mix is None:
        raise HTTPException(409, "Mix the episode first: the video follows the mix")
    why = vplan.check(cuts, float(mix["duration_s"]), cameras(vp, ep), vplan.wide_spans(plan))
    if why:
        raise HTTPException(422, why)
    host_ids = [h["id"] for h in hosts_in_order(vp, ep)]
    return vplan.annotate(cuts, mix["timeline"], plan.get("segments") or [], host_ids)


def stale_from(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> str | None:
    """The first finished stage whose inputs changed under it (a newly approved portrait, a new set, a
    new mix), so "continue" knows where to pick up again."""
    for h in hosts_in_order(vp, ep):
        entry = vp.hosts[h["id"]]
        g = usable_portrait(db, entry)
        if g is None or (entry.get("sheets") or {}).get("from") != g.id:
            return "cast"
    n = len(hosts_in_order(vp, ep))
    g = establishing(db, vp)
    if g is None or g.status != "approved" or (g.params or {}).get("vp_set") != set_key(vp, n):
        return "set"
    shots = refs(vp).get("angle_shots") or {}
    for key, host_id, angle in angle_keys(vp, ep):
        f = frame_of(db, shots.get(key))
        ids, _ = angle_refs(db, vp, ep, host_id, angle)
        if f is None or (f.params or {}).get("vp_refs") != angle_tag(ids, angle_prompt(db, vp, ep, host_id, angle)):
            return "keyframes"
    mix = mix_of(ep)
    if mix is not None and (plan_of(vp) is None or stage(vp, "plan").get("basis") != mix["generation_id"]):
        return "plan"
    return None


# ---------------------------------------------------------------- track audio

def track_folder(vp: VideoPodcast) -> Path:
    return get_settings().data_dir / "workspaces" / vp.workspace_id / "projects" / vp.project_id / "podcast" / "tracks"


def track_lines(track: dict, timeline: list[dict]) -> list[dict]:
    want = set(track.get("lines") or [])
    return sorted((it for it in timeline if it.get("line_id") in want), key=lambda it: float(it["start"]))


def audio_key(track: dict, timeline: list[dict], takes: dict[str, str]) -> str:
    rows = [[it["line_id"], takes.get(it["line_id"]), round(float(it["start"]), 3), round(float(it["end"]), 3)]
            for it in track_lines(track, timeline)]
    return hashlib.sha1(json.dumps([track["start"], track["end"], rows]).encode()).hexdigest()[:16]


def track_audio(db: Session, vp: VideoPodcast, track: dict, timeline: list[dict], takes: dict[str, str]) -> Path:
    """The host's dry voice on the segment's own clock: each of their line takes at its offset in the mix,
    silence everywhere else, exactly the track's length. Named by its content, so a new take makes a new file."""
    dst = track_folder(vp) / f"{track['id']}_{audio_key(track, timeline, takes)}.wav"
    if dst.is_file():
        return dst
    length = float(track["duration"])
    args: list[str] = []
    parts: list[str] = []
    for it in track_lines(track, timeline):
        g = db.get(Generation, takes.get(it["line_id"]) or "")
        src = generation_file(g) if g is not None else None
        if src is None or not src.is_file():
            raise VideoPodcastError("A line's voice take is gone; render the episode's lines and mix again")
        span = float(it["end"]) - float(it["start"])
        off = float(it["start"]) - track["start"]
        skip = max(0.0, -off)
        if span - skip <= 0 or off >= length:
            continue
        n = len(parts)
        args += ["-i", str(src)]
        parts.append(f"[{n}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
                     f"atrim=start={skip:.4f}:end={span:.4f},asetpts=PTS-STARTPTS,"
                     f"adelay=delays={round(max(0.0, off) * 1000)}:all=1[l{n}]")
    fit = f"apad=whole_dur={length:.4f},atrim=end={length:.4f}"
    if not parts:
        args += ["-f", "lavfi", "-t", f"{length:.4f}", "-i", "anullsrc=r=48000:cl=mono"]
        graph = f"[0:a]{fit}[out]"
    elif len(parts) == 1:
        graph = f"{parts[0]};[l0]{fit}[out]"
    else:
        lanes = "".join(f"[l{k}]" for k in range(len(parts)))
        graph = ";".join(parts) + f";{lanes}amix=inputs={len(parts)}:duration=longest:normalize=0,{fit}[out]"
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(".part.wav")
    rl.run_ff([*args, "-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
               str(part)])
    part.replace(dst)
    return dst


def pauses(db: Session, vp: VideoPodcast, ep: PodcastEpisode, plan: dict) -> list[float]:
    """Breaths inside a host's own lines (silencedetect on their track audio): more places a cut may land."""
    timeline = (mix_of(ep) or {}).get("timeline") or []
    takes = line_takes(db, ep)
    out: list[float] = []
    for t in plan["tracks"]:
        if t["kind"] != "host" or not t.get("lines"):
            continue
        try:
            silences = aa.analyse(track_audio(db, vp, t, timeline, takes)).get("silences") or []
        except (VideoPodcastError, rl.FFmpegError, aa.AnalysisError) as e:
            log.warning("no pauses for track %s: %s", t["id"], e)
            continue
        lines = track_lines(t, timeline)
        for s in silences:
            mid = t["start"] + (s["start"] + s["end"]) / 2
            if any(float(it["start"]) + 0.15 < mid < float(it["end"]) - 0.15 for it in lines):
                out.append(round(mid, 4))
    return out


# ---------------------------------------------------------------- takes

def takes_for(db: Session, shot_ids: list[str]) -> dict[str, dict[str, list[Generation]]]:
    """track id -> quality -> takes, newest first."""
    out: dict[str, dict[str, list[Generation]]] = {sid: {q: [] for q in QUALITIES} for sid in shot_ids}
    if not shot_ids:
        return out
    rows = db.scalars(select(Generation).where(
        Generation.target_type == "shot", Generation.target_id.in_(shot_ids), Generation.kind == "take",
    ).order_by(Generation.created_at.desc())).all()
    for g in rows:
        q = (g.params or {}).get("vp_quality")
        if q in QUALITIES:
            out[g.target_id][q].append(g)
    return out


def signature(track: dict, quality: str, aspect: str, frames: dict[str, Generation], audio: str | None) -> str:
    frame = frames.get(track["angle_key"])
    blob = [track["kind"], track["camera"], track["start"], track["end"], frame.id if frame else None,
            audio if track["kind"] == "host" else None, aspect, quality, PIECE_S]
    return hashlib.sha1(json.dumps(blob).encode()).hexdigest()[:16]


def needs(newest: Generation | None, sig: str) -> bool:
    if newest is None or newest.status in ("failed", "rejected"):
        return True
    if newest.status in LIVE:
        return False
    return (newest.params or {}).get("sig") != sig


class Inputs:
    """What the tracks' signatures are made of, read once per request."""

    def __init__(self, db: Session, vp: VideoPodcast, ep: PodcastEpisode):
        self.frames = angle_frames(db, vp)
        self.takes = line_takes(db, ep)
        self.timeline = (mix_of(ep) or {}).get("timeline") or []
        self.aspect = settings_of(vp)["aspect"]

    def sig(self, track: dict, quality: str) -> str:
        audio = audio_key(track, self.timeline, self.takes) if track["kind"] == "host" else None
        return signature(track, quality, self.aspect, self.frames, audio)


def used_tracks(vp: VideoPodcast) -> list[dict]:
    """Every host camera rolls (a cut may switch to it later); a wide take only when a cut shows it."""
    wides = [c for c in cuts_of(vp) if c["camera"] == vplan.WIDE]
    return [t for t in tracks_of(vp) if t["kind"] == "host" or any(
        c["start"] < t["end"] - 1e-6 and c["end"] > t["start"] + 1e-6 for c in wides)]


def targets(db: Session, vp: VideoPodcast, ep: PodcastEpisode, quality: str,
            track_ids: list[str] | None = None) -> list[dict]:
    if track_ids:
        wanted = set(track_ids)
        return [t for t in tracks_of(vp) if t["id"] in wanted]
    tracks = used_tracks(vp)
    inp = Inputs(db, vp, ep)
    have = takes_for(db, [t["id"] for t in tracks])
    return [t for t in tracks if needs(next(iter(have[t["id"]][quality]), None), inp.sig(t, quality))]


def take_prompt(db: Session, vp: VideoPodcast, ep: PodcastEpisode, track: dict) -> str:
    names = host_names(db, vp, ep)
    place = SET_PRESETS.get(settings_of(vp)["set_preset"], SET_PRESETS["modern_studio"])[0].lower()
    still = "Static camera, realistic podcast studio, natural light."
    if track["kind"] == "host":
        who = names.get(track["host_id"] or "", "The host")
        return (f"{who} in conversation at the {place}, natural gestures, small head movements, blinking; when "
                f"silent, listening attentively, small nods and reactions, mouth closed. {still}")
    together = " and ".join(names.values()) or "The hosts"
    return f"{together} in conversation at their microphones, natural gestures. Slow, steady camera. {still}"


def piece_prompt(db: Session, vp: VideoPodcast, ep: PodcastEpisode, track: dict, timeline: list[dict],
                 chunk: dict) -> str:
    """A host track's piece, with the words that host says in it. One prompt for the whole track ("natural
    gestures… when silent, mouth closed", no words) often left the mouth shut while the voice ran (24 Oct)."""
    names = host_names(db, vp, ep)
    who = names.get(track["host_id"] or "", "The host")
    place = SET_PRESETS.get(settings_of(vp)["set_preset"], SET_PRESETS["modern_studio"])[0].lower()
    still = "Static camera, realistic podcast studio, natural light."
    a, b = track["start"] + float(chunk.get("t_start") or 0.0), track["start"] + float(chunk.get("t_end") or 0.0)
    said = vplan.words_between(track_lines(track, timeline), a, b).replace('"', "'")
    if not said:
        return (f"{who} listens to the other host at the {place}, attentive, small nods and reactions, mouth "
                f"closed. {still}")
    from app.character_looks import performance_text
    from app.models import Character

    host = db.get(Character, ((vp.hosts or {}).get(track["host_id"] or "") or {}).get("character_id") or "")
    manner = performance_text(host).rstrip(".") or "natural small head movements, calm hand gestures"
    return (f'{who} talks with the other host at the {place} and says: "{said}" The mouth moves with every '
            f"word, {manner}; between sentences {who} listens with the mouth closed. {still}")


def first_unfinished(g: Generation) -> int | None:
    """The piece a failed or cancelled track picks up from, when some of its pieces are already on disk."""
    chunks = (g.params or {}).get("chunks") or []
    if not any(c.get("status") == "done" and c.get("file") for c in chunks):
        return None
    return next((c["idx"] for c in chunks if c.get("status") != "done"), None)


def enqueue_track(db: Session, vp: VideoPodcast, ep: PodcastEpisode, track: dict, quality: str, inp: Inputs,
                  parent_job_id: str | None, *, base: Generation | None = None, from_idx: int = 0) -> Generation:
    """One take of a camera track. With `base`, its pieces before `from_idx` are kept and the rest rendered."""
    frame = inp.frames.get(track["angle_key"])
    if frame is None:
        raise VideoPodcastError(f"The {track['angle_key']} angle has no frame yet")
    dur = float(track["duration"])
    sig = inp.sig(track, quality)
    common = {"aspect_ratio": inp.aspect, "duration_s": dur, "fps": 24.0, "first_frame_id": frame.id, "vp": vp.id,
              "vp_track": track["id"], "vp_quality": quality, "sig": sig, "created_by": {"flow": "video_podcast"}}
    prompt = take_prompt(db, vp, ep, track)
    if track["kind"] != "host":
        params = {**common, "num_frames": min(frames_for(dur, 24.0), MAX_FRAMES)}
    elif base is not None:
        params = {**longtake.reroll_params(base, from_idx, None, None), "sig": sig}
    else:
        audio = track_audio(db, vp, track, inp.timeline, inp.takes)
        chunks = pieces_for(track)
        for c in chunks:
            # given, so the long-take runner doesn't ask the LLM for beats
            c["prompt"] = piece_prompt(db, vp, ep, track, inp.timeline, c)
        params = {**common, "num_frames": frames_for(dur, 24.0), "longtake": True, "continuity": "extend",
                  "context_frames": longtake.context_frames("extend"), "chunks": chunks,
                  "assembly": {"status": "pending"}, "lipsync": True, "speech_audio": str(audio),
                  "guide_strength": GUIDE_STRENGTH}
    if quality == "final":
        params["quality"] = "hq"
    g = enqueue_generation(db, workspace_id=vp.workspace_id, project_id=vp.project_id, target_type="shot",
                           target_id=track["id"], kind="take", prompt=prompt, params=params,
                           parent_id=base.id if base is not None else None)
    if base is not None:
        longtake.copy_kept_chunks(base, g)
    if parent_job_id:
        job = db.get(Job, g.job_id)
        job.payload = {**(job.payload or {}), "parent_job_id": parent_job_id}
    return g


def take_to_cut(gens: dict[str, list[Generation]], quality: str) -> tuple[Generation | None, bool]:
    """The take a cut at this quality uses: its newest finished one, else (final) the draft's. (take, fallback)"""
    mine = next((g for g in gens[quality] if g.status in FINISHED and g.file_path), None)
    if mine is not None or quality == "draft":
        return mine, False
    draft = next((g for g in gens["draft"] if g.status in FINISHED and g.file_path), None)
    return draft, draft is not None


def progress(g: Generation | None) -> dict | None:
    if g is None:
        return None
    chunks = (g.params or {}).get("chunks") or []
    if not chunks:
        return {"pieces": 1, "done": 1 if g.status in FINISHED else 0,
                "current": 1 if g.status == "generating" else None,
                "failed": 1 if g.status == "failed" else None, "status": g.status}
    return {"pieces": len(chunks), "done": sum(1 for c in chunks if c.get("status") == "done"),
            "current": next((c["idx"] + 1 for c in chunks if c.get("status") == "generating"), None),
            "failed": next((c["idx"] + 1 for c in chunks if c.get("status") == "failed"), None), "status": g.status}


# ---------------------------------------------------------------- estimate

def estimate(db: Session, vp: VideoPodcast, ep: PodcastEpisode, quality: str,
             track_ids: list[str] | None = None) -> dict:
    todo = targets(db, vp, ep, quality, track_ids)
    measured = longtake.measured_rate(db, vp.workspace_id)
    gpu = generated = 0.0
    pieces = 0
    for t in todo:
        chunks = pieces_for(t)
        secs = sum(c["frames"] for c in chunks) / 24.0
        generated += secs
        gpu += secs * (IA2V_FACTOR if t["kind"] == "host" else 1.0)
        pieces += len(chunks)
    gpu = gpu * (measured or longtake.GPU_S_PER_OUTPUT_S) * QUALITY_FACTOR[quality] + TAKE_OVERHEAD_S * pieces
    if todo:
        gpu += longtake.MODEL_LOAD_S
    low, high = int(gpu * 0.8), int(math.ceil(gpu * 1.25))
    return {"quality": quality, "tracks": len(todo), "takes": len(todo), "pieces": pieces,
            "seconds": round(sum(t["duration"] for t in todo), 2), "generated_s": round(generated, 2),
            "low_s": low, "high_s": high, "basis": "measured" if measured else "rough", "label": gpu_label(low, high)}


def gpu_label(low: float, high: float) -> str:
    mid = (low + high) / 2
    if mid <= 0:
        return "Nothing to render"
    if mid < 3600:
        return f"≈ {max(1, round(mid / 60))} min on the video GPU"
    hours = mid / 3600
    return f"≈ {hours:.1f} h on the video GPU" if hours < 10 else f"≈ {round(hours)} h on the video GPU"


# ---------------------------------------------------------------- Library output

def library_version(db: Session, vp: VideoPodcast, ep: PodcastEpisode, quality: str) -> tuple[MediaItem, Generation]:
    rec = (vp.outputs or {}).get(quality) or {}
    item = db.get(MediaItem, rec.get("media_id") or "")
    if item is None or item.workspace_id != vp.workspace_id:
        item = MediaItem(id=new_id(), workspace_id=vp.workspace_id, kind="video", origin="generated",
                         title=f"{ep.title} (AI video, {quality})"[:300], tags=["podcast", "ai-video"])
        db.add(item)
        db.flush()
    g = Generation(id=new_id(), workspace_id=vp.workspace_id, project_id=None, target_type="media",
                   target_id=item.id, kind="video", version=next_version(db, "media", item.id, "video"),
                   status="queued", prompt=ep.title, seed=new_seed(), parent_id=item.generation_id,
                   params={"episode_id": ep.id, "video_podcast_id": vp.id, "quality": quality, "has_audio": True,
                           "created_by": {"flow": "video_podcast"}})
    db.add(g)
    db.flush()
    if item.generation_id is None:
        item.generation_id = g.id
    lib.touch(item)
    return item, g


# ---------------------------------------------------------------- output shapes

def _gen(g: Generation | None) -> dict | None:
    return gen_out(g).model_dump(mode="json") if g is not None else None


def status_of(db: Session, vp: VideoPodcast) -> tuple[str, str | None]:
    if busy(db, vp) is not None:
        return "running", None
    for key, _ in STAGES:
        rec = stage(vp, key)
        if rec.get("status") == "review":
            return "review", {"cast": "hosts", "keyframes": "look"}.get(key, key)
    if any(stage(vp, k).get("status") == "failed" for k, _ in STAGES):
        return "failed", None
    return "idle", None


def hosts_out(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> list[dict]:
    out = []
    for h in hosts_in_order(vp, ep):
        entry = vp.hosts[h["id"]]
        sheets = {}
        for view in ("front", "three_quarter"):
            g = db.get(Generation, ((entry.get("sheets") or {}).get(view)) or "")
            if g is not None:
                sheets[view] = _gen(g)
        photo = db.get(MediaItem, entry.get("photo_media_id") or "") if entry.get("photo_media_id") else None
        out.append({"host_id": h["id"], "name": h["name"], "role": h.get("role"), "persona": h.get("persona") or "",
                    "source": entry.get("source") or "persona", "character_id": entry["character_id"],
                    "photo_media_id": entry.get("photo_media_id"),
                    "photo_as_is": bool(entry.get("as_is")),
                    "photo_url": sv.item_url(db, photo.id) if photo else None,
                    "from_character_id": entry.get("from_character_id"), "look_id": entry.get("look_id"),
                    "consent": entry.get("consent"),
                    "portrait": _gen(current_portrait(db, entry, show=True)), "sheet_views": sheets})
    return out


def plan_out(db: Session, vp: VideoPodcast, ep: PodcastEpisode) -> dict:
    plan = plan_of(vp) or {}
    tracks = plan.get("tracks") or []
    inp = Inputs(db, vp, ep)
    have = takes_for(db, [t["id"] for t in tracks])
    used = {t["id"] for t in used_tracks(vp)}
    thumb = {k: gen_out(g).thumb_url for k, g in inp.frames.items()}
    out_tracks = []
    for t in tracks:
        gens = have.get(t["id"]) or {q: [] for q in QUALITIES}
        newest = {q: next(iter(gens[q]), None) for q in QUALITIES}
        out_tracks.append({**{k: t.get(k) for k in ("id", "kind", "camera", "host_id", "segment_id", "angle_key",
                                                    "start", "end", "duration", "pieces", "lines")},
                           "used": t["id"] in used, "thumb_url": thumb.get(t["angle_key"]),
                           "takes": {q: _gen(newest[q]) for q in QUALITIES},
                           "progress": {q: progress(newest[q]) for q in QUALITIES},
                           "needs": {q: t["id"] in used and needs(newest[q], inp.sig(t, q)) for q in QUALITIES}})
    names = host_names(db, vp, ep)
    cams = [{"key": hid, "label": name, "kind": "host", "host_id": hid,
             "thumb_url": thumb.get(vplan.angle_key(hid, vplan.TRACK_ANGLE))} for hid, name in names.items()]
    cams += [{"key": vplan.WIDE, "label": "Wide", "kind": "wide", "host_id": None, "thumb_url": thumb.get(vplan.WIDE)},
             {"key": vplan.COVER, "label": "Cover", "kind": "cover", "host_id": None, "thumb_url": None}]
    return {"segments": plan.get("segments") or [], "tracks": out_tracks, "cuts": plan.get("cuts") or [],
            "cameras": cams, "snaps": plan.get("snaps") or []}


def output_out(db: Session, rec: dict | None) -> dict | None:
    if not rec or not rec.get("generation_id"):
        return None
    g = db.get(Generation, rec["generation_id"])
    if g is None or g.status not in FINISHED or not g.file_path:
        return None
    return {**rec, "url": media_url(g.file_path)}


def vp_out(db: Session, vp: VideoPodcast) -> dict:
    ep = episode_of(db, vp)
    status, review = status_of(db, vp)
    mix = mix_of(ep)
    st = settings_of(vp)
    loc = db.get(Location, vp.set_location_id or "")
    shots = refs(vp).get("angle_shots") or {}
    angles = []
    names = host_names(db, vp, ep)
    for key, host_id, angle in angle_keys(vp, ep):
        label = ANGLE_LABELS[angle]
        angles.append({"key": key, "label": f"{names.get(host_id, '')} · {label}" if host_id else label,
                       "host_id": host_id, "angle": angle, "shot_id": shots.get(key),
                       "frame": _gen(frame_of(db, shots.get(key), show=True))})
    job = db.get(Job, vp.job_id) if vp.job_id else None
    return {
        "id": vp.id, "episode_id": vp.episode_id, "project_id": vp.project_id, "title": ep.title,
        "language": ep.language, "stage": vp.stage, "status": status, "review": review,
        "stages": [{"key": k, "label": label, "status": stage(vp, k).get("status", "pending"),
                    "detail": stage(vp, k).get("detail"), "started_at": stage(vp, k).get("started_at"),
                    "finished_at": stage(vp, k).get("finished_at")} for k, label in STAGES],
        "settings": st, "hosts": hosts_out(db, vp, ep),
        "set": {"location_id": vp.set_location_id, "preset": st["set_preset"], "prompt": st["set_prompt"],
                "description": loc.description if loc else set_description(vp),
                "establishing": _gen(establishing(db, vp, fresh_only=False, show=True))},
        "angles": angles, "plan": plan_out(db, vp, ep), "plan_stats": vplan.stats(plan_of(vp) or {}),
        "duration_s": mix["duration_s"] if mix else None,
        "mix_stale": bool(mix and plan_of(vp) and stage(vp, "plan").get("basis") not in (None, mix["generation_id"])),
        "outputs": {q: output_out(db, (vp.outputs or {}).get(q)) for q in QUALITIES},
        "job": job_out(job).model_dump(mode="json") if job is not None else None,
        "eta_s": ((job.result or {}).get("eta_s") if job is not None and job.status in ("queued", "running") else None),
        "created_at": vp.created_at, "updated_at": vp.updated_at,
    }


def other_characters(db: Session, vp: VideoPodcast) -> list[dict]:
    # Character Studio characters (no project) come along too; they're who people cast across shows
    rows = db.execute(select(Character, Project).outerjoin(Project, Project.id == Character.project_id).where(
        Character.workspace_id == vp.workspace_id,
        or_(Character.project_id.is_(None), Character.project_id != vp.project_id),
    ).order_by(Character.project_id.is_not(None), Character.updated_at.desc()).limit(200)).all()
    out = []
    for c, p in rows:
        g = sb.approved(db, "character", c.id, "portrait") or next(
            (x for x in portraits(db, c.id) if x.status == "ready" and x.file_path), None)
        out.append({"id": c.id, "name": c.name, "description": c.description, "project_id": p.id if p else None,
                    "project_title": p.title if p else "Character Studio", "studio": p is None,
                    "portrait_url": media_url(g.file_path) if g is not None else None,
                    "thumb_url": gen_out(g).thumb_url if g is not None else None})
    return out
