"""Shows and episodes (contract v20): hosts and the script's cast, chapters, the output records and the
Library items an episode makes (its master, the published file, the video)."""
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import dialogue as dl
from app import library as lib
from app.models import (Character, CharacterLook, Generation, Job, MediaItem, PodcastEpisode, PodcastShow, VideoPodcast, VoiceScript, new_id)
from app.services import job_out, media_url, new_seed, next_version

WRITE_JOB = "podcast_write"
MIX_JOB = "podcast_mix"
PUBLISH_JOB = "podcast_publish"
VIDEO_JOB = "podcast_video"

FORMATS = ("interview", "conversation", "debate", "solo", "news", "storytelling", "qa")
ROLES = ("host", "cohost", "guest", "narrator")
MAX_HOSTS = 4
WPM = 150
FINISHED = ("ready", "approved")

DEFAULT_SETTINGS = {
    "intro": {"media_id": None, "lead_s": 4.0, "overlap_s": 3.0, "gain_db": 0.0},
    "outro": {"media_id": None, "overlap_s": 2.0, "gain_db": 0.0, "max_s": 30.0},
    "bed": {"media_id": None, "gain_db": -20.0},
    "duck_db": 12.0, "gap_s": 0.35, "chapter_gap_s": 1.0, "loudness": "stereo",
}


def short(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def settings_of(ep: PodcastEpisode) -> dict:
    cur = ep.settings or {}
    out = {}
    for key, default in DEFAULT_SETTINGS.items():
        if isinstance(default, dict):
            out[key] = {**default, **(cur.get(key) or {})}
        else:
            out[key] = cur.get(key, default)
    return out


# ---------------------------------------------------------------- hosts, cast and chapters

def clean_hosts(hosts: list[dict] | None) -> list[dict]:
    out, seen = [], set()
    for h in (hosts or [])[:MAX_HOSTS]:
        hid = str(h.get("id") or "").strip() or short("h_")
        if hid in seen or hid == dl.NARRATOR:
            hid = short("h_")
        seen.add(hid)
        role = h.get("role") if h.get("role") in ROLES else "host"
        me = bool(h.get("me"))
        out.append({"id": hid, "name": " ".join(str(h.get("name") or "").split())[:80] or "Host", "role": role,
                    "persona": " ".join(str(h.get("persona") or "").split())[:400],
                    "voice_id": None if me else (h.get("voice_id") or None), "me": me,
                    "character_id": h.get("character_id") or None,
                    "look_id": (h.get("look_id") or None) if h.get("character_id") else None})
    return out


def check_hosts(db: Session, workspace_id: str, hosts: list[dict]) -> None:
    """A host linked to a character must point at a Character Studio one of this workspace, in one of its looks."""
    for h in hosts:
        if not h.get("character_id"):
            continue
        c = db.get(Character, h["character_id"])
        if c is None or c.workspace_id != workspace_id:
            raise HTTPException(404, f"{h['name']}: that character doesn't exist")
        if c.project_id is not None:
            raise HTTPException(422, f"{h['name']}: pick a character from Character Studio")
        if h.get("look_id"):
            look = db.get(CharacterLook, h["look_id"])
            if look is None or look.character_id != c.id:
                raise HTTPException(422, f"{h['name']}: that look isn't one of {c.name}'s")


def clean_chapters(chapters: list[dict] | None) -> list[dict]:
    out, seen = [], set()
    for c in chapters or []:
        cid = str(c.get("id") or "").strip() or short("ch_")
        if cid in seen:
            cid = short("ch_")
        seen.add(cid)
        out.append({"id": cid, "title": " ".join(str(c.get("title") or "").split())[:200] or "Chapter"})
    return out


def host_for(name: str, hosts: list[dict], fallback: int = 0) -> dict | None:
    """The host an LLM (or a pasted script) meant by `name`: exact name, then first name, then role."""
    if not hosts:
        return None
    key = " ".join((name or "").split()).casefold()
    for h in hosts:
        if h["name"].casefold() == key:
            return h
    for h in hosts:
        if key and h["name"].casefold().split()[0] == key.split()[0]:
            return h
    for h in hosts:
        if key in (h["role"], h["role"].replace("co", "co-")):
            return h
    return hosts[fallback % len(hosts)]


def sync_cast(s: VoiceScript, hosts: list[dict]) -> None:
    """Hosts are cast members with the same id. A "me" host has no voice and its lines are recordings."""
    cast = dl.clean_cast(s.cast)
    by_id = {c["id"]: c for c in cast}
    for h in hosts:
        voice = None if h.get("me") else h.get("voice_id")
        if h["id"] in by_id:
            by_id[h["id"]].update(name=h["name"], voice_id=voice)
        else:
            cast.append({"id": h["id"], "name": h["name"], "voice_id": voice, "color": None})
    s.cast = dl.clean_cast(cast)
    me = {h["id"] for h in hosts if h.get("me")}
    lines = []
    for ln in s.lines or []:
        kind = dl.kind_of(ln)
        if ln.get("speaker_id") in me and kind == "speech":
            ln = {**ln, "kind": "recording"}
        elif ln.get("speaker_id") not in me and kind == "recording" and not ln.get("media_id"):
            ln = {k: v for k, v in ln.items() if k != "kind"}
        lines.append(ln)
    s.lines = lines


def ensure_script(db: Session, ep: PodcastEpisode) -> VoiceScript:
    s = db.get(VoiceScript, ep.script_id or "")
    if s is None or s.workspace_id != ep.workspace_id:
        # the podcast mix does its own loudness, so the plain Dialogue mix of these lines stays unlevelled
        s = VoiceScript(workspace_id=ep.workspace_id, title=ep.title[:200], language=ep.language, engine="auto",
                        cast=dl.clean_cast([]), lines=[], settings={**dl.DEFAULT_SETTINGS, "loudness": "none"})
        db.add(s)
        db.flush()
        ep.script_id = s.id
    sync_cast(s, ep.hosts or [])
    return s


def new_line(host: dict | None, text: str, chapter_id: str | None, *, para: bool = False) -> dict:
    ln = dl._line(host["id"] if host else dl.NARRATOR, " ".join(text.split()), para=para)
    if chapter_id:
        ln["chapter_id"] = chapter_id
    if host and host.get("me"):
        ln["kind"] = "recording"
    return ln


def lines_from_chapters(chapters: list[dict], hosts: list[dict]) -> tuple[list[dict], list[dict]]:
    """[{title, lines: [{host, text}]}] -> (episode chapters, script lines)."""
    out_ch, out_lines = [], []
    for c in chapters:
        rows = [r for r in c.get("lines") or [] if " ".join(str(r.get("text") or "").split())]
        if not rows:
            continue
        cid = short("ch_")
        out_ch.append({"id": cid, "title": " ".join(str(c.get("title") or "").split())[:200] or "Chapter"})
        for i, r in enumerate(rows):
            out_lines.append(new_line(host_for(str(r.get("host") or ""), hosts, i), str(r["text"]), cid,
                                      para=i == len(rows) - 1))
    return out_ch, out_lines


_HEADING = re.compile(r"^\s*#{1,3}\s+(.+?)\s*$")


def parse_script(text: str, hosts: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """A pasted script: `NAME: words` lines, `# Title` starting a chapter. New names become hosts while
    there's room. Returns (hosts, chapters, lines)."""
    hosts = [dict(h) for h in hosts]
    sections: list[tuple[str, list[str]]] = []
    for row in text.replace("\r\n", "\n").split("\n"):
        m = _HEADING.match(row)
        if m:
            sections.append((m[1], []))
            continue
        if not sections:
            sections.append(("", []))
        sections[-1][1].append(row)
    chapters, lines = [], []
    for title, rows in sections:
        body = "\n".join(rows).strip()
        if not body:
            continue
        cast = [{"id": h["id"], "name": h["name"], "voice_id": None, "color": None} for h in hosts]
        cast = dl.clean_cast(cast)
        # a solo show's unprefixed lines are the host's, not the narrator's
        new = dl.split(body, "lines", cast, hosts[0]["id"] if len(hosts) == 1 else None)
        if not new:
            continue
        for c in cast:
            if c["id"] != dl.NARRATOR and not any(h["id"] == c["id"] for h in hosts) and len(hosts) < MAX_HOSTS:
                hosts.append({"id": c["id"], "name": c["name"], "role": "cohost" if hosts else "host", "persona": "",
                              "voice_id": None, "me": False})
        known = {h["id"]: h for h in hosts}
        cid = short("ch_")
        chapters.append({"id": cid, "title": title or (f"Part {len(chapters) + 1}" if len(sections) > 1 else "Episode")})
        names = {c["id"]: c["name"] for c in cast}
        for ln in new:
            h = known.get(ln["speaker_id"])
            if h is None and ln["speaker_id"] != dl.NARRATOR:
                # more speakers than hosts: the closest host takes the line
                h = host_for(names.get(ln["speaker_id"], ""), hosts)
            lines.append(new_line(h, ln["text"], cid, para=ln["para"]) if h else
                         {**ln, "chapter_id": cid})
    return hosts, chapters, lines


def word_count(lines: list[dict]) -> int:
    return sum(len((ln.get("text") or "").split()) for ln in lines
               if dl.kind_of(ln) in ("speech", "recording") or dl.needs_tts(ln))


# ---------------------------------------------------------------- output

def item_url(db: Session, media_id: str | None) -> str | None:
    item = db.get(MediaItem, media_id) if media_id else None
    g = db.get(Generation, item.generation_id) if item is not None and item.generation_id else None
    return media_url(g.file_path) if g is not None and g.status in FINISHED and g.file_path else None


def _jobs(db: Session, ep: PodcastEpisode) -> dict:
    ids = (ep.outputs or {}).get("jobs") or {}
    out = {}
    for key, jid in ids.items():
        j = db.get(Job, jid) if jid else None
        if j is not None:
            out[key] = job_out(j)
    return out


def set_job(ep: PodcastEpisode, key: str, job: Job) -> None:
    outs = dict(ep.outputs or {})
    outs["jobs"] = {**(outs.get("jobs") or {}), key: job.id}
    ep.outputs = outs


def _output(db: Session, rec: dict | None) -> dict | None:
    if not rec or not rec.get("generation_id"):
        return None
    g = db.get(Generation, rec["generation_id"])
    if g is None or g.status not in FINISHED or not g.file_path:
        return None
    return {**rec, "url": media_url(g.file_path)}


def sources_out(ep: PodcastEpisode, s: VoiceScript | None) -> dict:
    src = ep.sources or {}
    docs = [{k: v for k, v in d.items() if k != "text"} for d in src.get("docs") or []]
    return {"topic": src.get("topic") or "", "text": src.get("text") or "", "docs": docs,
            "has_script": bool(s is not None and s.lines)}


def medium_of(ep: PodcastEpisode, has_video: bool) -> str:
    # an audio episode someone already made an AI video of (before Podcast Studio) counts as video
    return "video" if has_video or (ep.settings or {}).get("medium") == "video" else "audio"


def with_video(db: Session, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    return set(db.scalars(select(VideoPodcast.episode_id).where(VideoPodcast.episode_id.in_(ids))).all())


def episode_out(db: Session, ep: PodcastEpisode) -> dict:
    from app.podcast import produce

    s = db.get(VoiceScript, ep.script_id or "") if ep.script_id else None
    lines = dl.refresh(db, s) if s is not None else []
    words = word_count(lines)
    outs = ep.outputs or {}
    mix = _output(db, outs.get("mix"))
    if mix is not None:
        try:
            basis = produce.plan(s, ep, lines)["basis"] if s is not None else None
        except produce.ProduceError:
            basis = None
        mix["stale"] = basis != mix.get("basis") or bool(dl.not_ready(lines))
    show = db.get(PodcastShow, ep.show_id) if ep.show_id else None
    return {"id": ep.id, "show_id": ep.show_id, "show_name": show.name if show else None, "title": ep.title,
            "format": ep.format, "medium": medium_of(ep, bool(with_video(db, [ep.id]))), "language": ep.language,
            "target_minutes": ep.target_minutes, "step": ep.step, "cover_media_id": ep.cover_media_id, "cover_url": item_url(db, ep.cover_media_id),
            "sources": sources_out(ep, s), "hosts": ep.hosts or [], "chapters": ep.chapters or [],
            "script_id": ep.script_id, "settings": settings_of(ep), "notes": ep.notes,
            "outputs": {"mix": mix, "publish": _output(db, outs.get("publish")),
                        "video": _output(db, outs.get("video"))},
            "jobs": _jobs(db, ep), "words": words, "minutes": round(words / WPM, 1),
            "counts": dl.counts(lines), "created_at": ep.created_at, "updated_at": ep.updated_at}


def episode_summary(db: Session, ep: PodcastEpisode, has_video: bool = False) -> dict:
    s = db.get(VoiceScript, ep.script_id or "") if ep.script_id else None
    words = word_count(s.lines or []) if s is not None else 0
    return {"id": ep.id, "show_id": ep.show_id, "title": ep.title, "format": ep.format,
            "medium": medium_of(ep, has_video), "language": ep.language,
            "target_minutes": ep.target_minutes, "step": ep.step, "minutes": round(words / WPM, 1),
            "has_mix": bool((ep.outputs or {}).get("mix")), "published": bool((ep.outputs or {}).get("publish")),
            "created_at": ep.created_at, "updated_at": ep.updated_at}


def show_out(db: Session, show: PodcastShow) -> dict:
    n = db.scalar(select(func.count(PodcastEpisode.id)).where(PodcastEpisode.show_id == show.id)) or 0
    return {"id": show.id, "name": show.name, "description": show.description, "author": show.author,
            "language": show.language, "cover_media_id": show.cover_media_id,
            "cover_url": item_url(db, show.cover_media_id), "hosts": show.hosts or [],
            "music": show.music or {}, "ads": show.ads or [], "episodes": n,
            "created_at": show.created_at, "updated_at": show.updated_at}


def clean_ads(ads: list[dict] | None) -> list[dict]:
    out, seen = [], set()
    for a in ads or []:
        aid = str(a.get("id") or "").strip() or short("ad_")
        if aid in seen:
            aid = short("ad_")
        seen.add(aid)
        out.append({"id": aid, "name": " ".join(str(a.get("name") or "").split())[:120] or "Ad",
                    "kind": a.get("kind") if a.get("kind") in ("jingle", "sponsor") else "jingle",
                    "media_id": a.get("media_id") or None, "text": (a.get("text") or "").strip()[:3000] or None,
                    "host_id": a.get("host_id") or None})
    return out


# ---------------------------------------------------------------- media checks and Library outputs

def audio_item(db: Session, workspace_id: str, media_id: str, what: str = "clip") -> MediaItem:
    item = db.get(MediaItem, media_id) if media_id else None
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(404, f"That {what} isn't in the Library")
    if item.kind != "audio":
        raise HTTPException(422, f"The {what} has to be an audio clip")
    g = db.get(Generation, item.generation_id or "")
    if g is None or g.status not in FINISHED or not g.file_path:
        raise HTTPException(409, f"The {what} isn't finished yet")
    return item


def image_item(db: Session, workspace_id: str, media_id: str | None) -> MediaItem | None:
    if not media_id:
        return None
    item = db.get(MediaItem, media_id)
    if item is None or item.workspace_id != workspace_id:
        raise HTTPException(404, "That cover image isn't in the Library")
    if item.kind != "image":
        raise HTTPException(422, "The cover has to be an image")
    return item


def library_version(db: Session, ep: PodcastEpisode, key: str, kind: str, title: str, tags: list[str],
                    params: dict) -> tuple[MediaItem, Generation]:
    """A new queued version of the episode's `key` Library item (made the first time)."""
    rec = (ep.outputs or {}).get(key) or {}
    item = db.get(MediaItem, rec.get("media_id") or "")
    if item is None or item.workspace_id != ep.workspace_id or item.kind != kind:
        item = MediaItem(id=new_id(), workspace_id=ep.workspace_id, kind=kind, origin="generated",
                         title=title[:300], tags=tags)
        db.add(item)
        db.flush()
    g = Generation(id=new_id(), workspace_id=ep.workspace_id, project_id=None, target_type="media",
                   target_id=item.id, kind=kind, version=next_version(db, "media", item.id, kind),
                   status="queued", prompt=ep.title, seed=new_seed(), parent_id=item.generation_id, params=params)
    db.add(g)
    db.flush()
    if item.generation_id is None:
        item.generation_id = g.id
    lib.touch(item)
    return item, g


def queue_job(db: Session, ep: PodcastEpisode, job_type: str, key: str, message: str, payload: dict,
              generation: Generation | None = None) -> Job:
    running = ((ep.outputs or {}).get("jobs") or {}).get(key)
    j = db.get(Job, running) if running else None
    if j is not None and j.status in ("queued", "running"):
        raise HTTPException(409, "That's already running; wait for it to finish")
    job = Job(workspace_id=ep.workspace_id, type=job_type, generation_id=generation.id if generation else None,
              message=f"Waiting for a worker · {message}", payload={"episode_id": ep.id, **payload})
    db.add(job)
    db.flush()
    if generation is not None:
        generation.job_id = job.id
    set_job(ep, key, job)
    return job
