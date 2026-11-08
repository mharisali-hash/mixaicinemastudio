"""Podcast Studio (contract v20): shows, episodes, sources, the writer, lines, the mix and publishing."""
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import visibility as vis
from app import dialogue as dl
from app import library as lib
from app.db import get_db
from app.dialogue_schemas import RenderIn, RenderOut
from app.models import MediaItem, PodcastEpisode, PodcastShow, VoiceScript
from app.podcast import ingest, produce, publish
from app.podcast import service as sv
from app.podcast import video as vd
from app.podcast.schemas import (Ad, EditIn, EpisodeIn, EpisodeOut, EpisodePatch, EpisodeSummary, InsertIn,
                                 OutputsOut, PublishIn, RecordIn, ScriptTextIn, ShowIn, ShowOut, ShowPatch, VideoIn)
from app.schemas import JobOut
from app.security import CurrentUser, get_current_user, require_editor
from app.services import get_owned, job_out

router = APIRouter(prefix="/podcast", tags=["podcast"])
MAX_DOCS = 10


def _show(db: Session, cur: CurrentUser, show_id: str) -> PodcastShow:
    return get_owned(db, PodcastShow, show_id, cur.workspace_id, "Show")


def _ep(db: Session, cur: CurrentUser, episode_id: str) -> PodcastEpisode:
    return get_owned(db, PodcastEpisode, episode_id, cur.workspace_id, "Episode")


def _script(db: Session, ep: PodcastEpisode) -> VoiceScript:
    s = db.get(VoiceScript, ep.script_id or "")
    if s is None or s.workspace_id != ep.workspace_id:
        raise HTTPException(409, "This episode has no script yet")
    return s


def _out(db: Session, ep: PodcastEpisode) -> EpisodeOut:
    return EpisodeOut(**sv.episode_out(db, ep))


def _media_ok(db: Session, cur: CurrentUser, media_id: str | None, kind: str, what: str) -> str | None:
    if not media_id:
        return None
    item = db.get(MediaItem, media_id)
    if item is None or item.workspace_id != cur.workspace_id:
        raise HTTPException(404, f"The {what} isn't in the Library")
    if item.kind != kind:
        raise HTTPException(422, f"The {what} has to be {'an image' if kind == 'image' else 'an audio clip'}")
    return item.id


def _music(db: Session, cur: CurrentUser, music: dict) -> dict:
    return {k: _media_ok(db, cur, music.get(k), "audio", "music") for k in
            ("intro_media_id", "outro_media_id", "bed_media_id")}


def _ads(db: Session, cur: CurrentUser, ads: list[dict]) -> list[dict]:
    for a in ads:
        _media_ok(db, cur, a.get("media_id"), "audio", "ad clip")
    return sv.clean_ads(ads)


# ---------------------------------------------------------------- shows

@router.get("/shows", response_model=list[ShowOut])
def list_shows(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(PodcastShow).where(PodcastShow.workspace_id == cur.workspace_id,
                                                *vis.where(PodcastShow, vis.wanted(cur)))
                      .order_by(PodcastShow.updated_at.desc(), PodcastShow.id)).all()
    return [ShowOut(**sv.show_out(db, s)) for s in rows]


@router.post("/shows", response_model=ShowOut, status_code=201)
def create_show(body: ShowIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    show = PodcastShow(workspace_id=cur.workspace_id, name=body.name.strip(), language=body.language.lower(),
                       description=body.description.strip(), author=body.author.strip(),
                       cover_media_id=_media_ok(db, cur, body.cover_media_id, "image", "cover"),
                       hosts=sv.clean_hosts([h.model_dump() for h in body.hosts]),
                       music=_music(db, cur, body.music.model_dump()),
                       ads=_ads(db, cur, [a.model_dump() for a in body.ads]))
    sv.check_hosts(db, cur.workspace_id, show.hosts)
    db.add(show)
    db.commit()
    return ShowOut(**sv.show_out(db, show))


@router.get("/shows/{show_id}", response_model=ShowOut)
def get_show(show_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return ShowOut(**sv.show_out(db, _show(db, cur, show_id)))


@router.patch("/shows/{show_id}", response_model=ShowOut)
def patch_show(show_id: str, body: ShowPatch, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    show = _show(db, cur, show_id)
    data = body.model_dump(exclude_unset=True)
    for key in ("name", "description", "author"):
        if data.get(key) is not None:
            setattr(show, key, data[key].strip())
    if data.get("language"):
        show.language = data["language"].lower()
    if "cover_media_id" in data:
        show.cover_media_id = _media_ok(db, cur, data["cover_media_id"], "image", "cover")
    if data.get("hosts") is not None:
        show.hosts = sv.clean_hosts(data["hosts"])
        sv.check_hosts(db, cur.workspace_id, show.hosts)
    if data.get("music") is not None:
        show.music = _music(db, cur, data["music"])
    if data.get("ads") is not None:
        show.ads = _ads(db, cur, data["ads"])
    db.commit()
    return ShowOut(**sv.show_out(db, show))


@router.delete("/shows/{show_id}", status_code=204)
def delete_show(show_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    show = _show(db, cur, show_id)
    # SQLite only honours ON DELETE SET NULL with its foreign keys switched on, so it's done here too
    for ep in db.scalars(select(PodcastEpisode).where(PodcastEpisode.show_id == show.id)):
        ep.show_id = None
    db.delete(show)
    db.commit()
    return Response(status_code=204)


@router.post("/shows/{show_id}/ads", response_model=ShowOut)
def add_ad(show_id: str, body: Ad, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    show = _show(db, cur, show_id)
    if not body.media_id and not (body.text or "").strip():
        raise HTTPException(422, "An ad needs a clip or the words of a sponsor read")
    show.ads = _ads(db, cur, [*(show.ads or []), body.model_dump()])
    db.commit()
    return ShowOut(**sv.show_out(db, show))


@router.delete("/shows/{show_id}/ads/{ad_id}", response_model=ShowOut)
def delete_ad(show_id: str, ad_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    show = _show(db, cur, show_id)
    show.ads = [a for a in show.ads or [] if a.get("id") != ad_id]
    db.commit()
    return ShowOut(**sv.show_out(db, show))


# ---------------------------------------------------------------- episodes

@router.get("/episodes", response_model=list[EpisodeSummary])
def list_episodes(show_id: str | None = Query(None), db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(get_current_user)):
    stmt = select(PodcastEpisode).where(PodcastEpisode.workspace_id == cur.workspace_id,
                                        *vis.where(PodcastEpisode, vis.wanted(cur)))
    if show_id:
        stmt = stmt.where(PodcastEpisode.show_id == show_id)
    rows = db.scalars(stmt.order_by(PodcastEpisode.updated_at.desc(), PodcastEpisode.id)).all()
    videos = sv.with_video(db, [ep.id for ep in rows])
    return [EpisodeSummary(**sv.episode_summary(db, ep, ep.id in videos)) for ep in rows]


@router.post("/episodes", response_model=EpisodeOut, status_code=201)
def create_episode(body: EpisodeIn, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    show = _show(db, cur, body.show_id) if body.show_id else None
    hosts = [h.model_dump() for h in body.hosts] if body.hosts is not None else list(show.hosts or []) if show else []
    music = (show.music or {}) if show else {}
    settings = {"intro": {"media_id": music.get("intro_media_id")}, "outro": {"media_id": music.get("outro_media_id")},
                "bed": {"media_id": music.get("bed_media_id")}, "medium": body.medium}
    hosts = sv.clean_hosts(hosts)
    sv.check_hosts(db, cur.workspace_id, hosts)
    ep = PodcastEpisode(workspace_id=cur.workspace_id, show_id=show.id if show else None, title=body.title.strip(),
                        format=body.format, language=(body.language or (show.language if show else "en")).lower(),
                        target_minutes=body.target_minutes, hosts=hosts,
                        sources={"topic": body.topic.strip(), "text": "", "docs": []}, chapters=[],
                        settings=settings, outputs={})
    db.add(ep)
    db.flush()
    if body.medium == "video":
        # its video steps follow straight on in the same stepper, so the AI video exists from the start
        vd.create(db, ep, None)
    db.commit()
    return _out(db, ep)


@router.get("/episodes/{episode_id}", response_model=EpisodeOut)
def get_episode(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    return _out(db, _ep(db, cur, episode_id))


@router.patch("/episodes/{episode_id}", response_model=EpisodeOut)
def patch_episode(episode_id: str, body: EpisodePatch, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    data = body.model_dump(exclude_unset=True)
    s = db.get(VoiceScript, ep.script_id or "") if ep.script_id else None
    if data.get("title"):
        ep.title = data["title"].strip()
        if s is not None:
            s.title = ep.title
    for key in ("format", "target_minutes", "step"):
        if data.get(key) is not None:
            setattr(ep, key, data[key])
    if data.get("language"):
        ep.language = data["language"].lower()
        if s is not None:
            s.language = ep.language
    if "show_id" in data:
        ep.show_id = _show(db, cur, data["show_id"]).id if data["show_id"] else None
    if "cover_media_id" in data:
        ep.cover_media_id = _media_ok(db, cur, data["cover_media_id"], "image", "cover")
    if data.get("sources") is not None:
        src = dict(ep.sources or {})
        for key in ("topic", "text"):
            if data["sources"].get(key) is not None:
                src[key] = data["sources"][key].strip()
        ep.sources = src
    if data.get("hosts") is not None:
        ep.hosts = sv.clean_hosts(data["hosts"])
        sv.check_hosts(db, cur.workspace_id, ep.hosts)
        if s is not None:
            sv.sync_cast(s, ep.hosts)
    if data.get("chapters") is not None:
        ep.chapters = sv.clean_chapters(data["chapters"])
    if data.get("settings") is not None:
        st = data["settings"]
        for key in produce.MUSIC:
            st[key]["media_id"] = _media_ok(db, cur, st[key].get("media_id"), "audio", f"{key} music")
        # written whole by the Produce step, which doesn't know the medium
        ep.settings = {**st, "medium": (ep.settings or {}).get("medium", "audio")}
    if "notes" in data:
        ep.notes = data["notes"]
    if data.get("medium") == "video":
        ep.settings = {**(ep.settings or {}), "medium": "video"}
    vp = vd.find(db, ep)
    if data.get("medium") == "video" and vp is None:
        vd.create(db, ep, None)
    elif vp is not None and data.get("hosts") is not None and vd.busy(db, vp) is None:
        # a running job keeps its hosts; continue picks the change up later
        vd.sync_hosts(db, vp, ep)
        if vd.apply_linked_hosts(db, vp, ep):
            vd.reset_from(vp, "cast")
    if s is not None:
        dl.refresh(db, s)
    db.commit()
    return _out(db, ep)


@router.delete("/episodes/{episode_id}", status_code=204)
def delete_episode(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    # the script stays (it's a Dialogue script too) and so does everything in the Library
    db.delete(_ep(db, cur, episode_id))
    db.commit()
    return Response(status_code=204)


@router.post("/episodes/{episode_id}/sources", response_model=EpisodeOut)
async def add_source(episode_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                     cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    src = dict(ep.sources or {})
    docs = list(src.get("docs") or [])
    if len(docs) >= MAX_DOCS:
        raise HTTPException(422, f"An episode can have up to {MAX_DOCS} documents")
    data = await file.read(ingest.MAX_BYTES + 1)
    try:
        doc = ingest.extract(file.filename or "document", data)
    except ingest.IngestError as e:
        raise HTTPException(422, str(e)) from None
    name = (file.filename or "Document").replace("\\", "/").rsplit("/", 1)[-1][:200]
    docs.append({"id": sv.short("doc_"), "name": name, **doc})
    src["docs"] = docs
    ep.sources = src
    db.commit()
    return _out(db, ep)


@router.delete("/episodes/{episode_id}/sources/{doc_id}", response_model=EpisodeOut)
def delete_source(episode_id: str, doc_id: str, db: Session = Depends(get_db),
                  cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    src = dict(ep.sources or {})
    src["docs"] = [d for d in src.get("docs") or [] if d.get("id") != doc_id]
    ep.sources = src
    db.commit()
    return _out(db, ep)


@router.post("/episodes/{episode_id}/script", response_model=EpisodeOut)
def paste_script(episode_id: str, body: ScriptTextIn, db: Session = Depends(get_db),
                 cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    hosts, chapters, lines = sv.parse_script(body.text, ep.hosts or [])
    if not lines:
        raise HTTPException(422, "There are no lines in that script")
    ep.hosts = hosts
    s = sv.ensure_script(db, ep)
    s.lines = lines
    ep.chapters = chapters
    sv.sync_cast(s, hosts)
    dl.refresh(db, s)
    db.commit()
    return _out(db, ep)


def _queue(db: Session, ep: PodcastEpisode, payload: dict, message: str) -> JobOut:
    job = sv.queue_job(db, ep, sv.WRITE_JOB, "write", message, payload)
    db.commit()
    return job_out(job)


@router.post("/episodes/{episode_id}/write", response_model=JobOut, status_code=202)
def write(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    if not ep.hosts:
        raise HTTPException(422, "Add at least one host first")
    src = ep.sources or {}
    if not (src.get("topic") or "").strip() and not ingest.material(src):
        raise HTTPException(422, "Give the episode a topic or some material first")
    return _queue(db, ep, {"op": "write"}, "Writing the episode")


@router.post("/episodes/{episode_id}/edit-lines", response_model=JobOut, status_code=202)
def edit_lines(episode_id: str, body: EditIn, db: Session = Depends(get_db),
               cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    s = _script(db, ep)
    if body.op == "rewrite_chapter":
        cid = body.chapter_id
        if not cid and body.line_ids:
            cid = next((ln.get("chapter_id") for ln in s.lines or [] if ln["id"] in set(body.line_ids)), None)
        if not cid or not any(ln.get("chapter_id") == cid for ln in s.lines or []):
            raise HTTPException(422, "Pick a chapter to rewrite")
        payload = {"op": "edit", "op_edit": body.op, "chapter_id": cid, "note": body.note}
    else:
        ids = set(body.line_ids or [])
        if not ids or not any(ln["id"] in ids for ln in s.lines or []):
            raise HTTPException(422, "Select the lines to change first")
        payload = {"op": "edit", "op_edit": body.op, "line_ids": list(body.line_ids or []), "note": body.note}
    return _queue(db, ep, payload, {"rewrite_chapter": "Rewriting the chapter", "shorter": "Shortening lines",
                                    "livelier": "Livening up lines"}[body.op])


@router.post("/episodes/{episode_id}/render", response_model=RenderOut, status_code=202)
def render(episode_id: str, body: RenderIn | None = None, db: Session = Depends(get_db),
           cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    s = _script(db, ep)
    jobs, failed = dl.queue_render(db, cur, s, body.line_ids if body else None)
    db.commit()
    return RenderOut(jobs=jobs, queued=len(jobs), failed=failed)


@router.post("/episodes/{episode_id}/record-line", response_model=EpisodeOut)
def record_line(episode_id: str, body: RecordIn, db: Session = Depends(get_db),
                cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    s = _script(db, ep)
    item = sv.audio_item(db, cur.workspace_id, body.media_id, "recording")
    if not any(ln["id"] == body.line_id for ln in s.lines or []):
        raise HTTPException(404, "Line not found")
    s.lines = [{**ln, "kind": "recording", "media_id": item.id} if ln["id"] == body.line_id else ln
               for ln in s.lines or []]
    # a take like any other: out of the Library grid, found by the script's tag
    item.tags = [dl.take_tag(s.id)]
    lib.touch(item)
    dl.refresh(db, s)
    db.commit()
    return _out(db, ep)


@router.post("/episodes/{episode_id}/insert", response_model=EpisodeOut)
def insert(episode_id: str, body: InsertIn, db: Session = Depends(get_db),
           cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    s = _script(db, ep)
    lines = list(s.lines or [])
    if body.after_line_id is None:
        at, chapter = 0, (lines[0].get("chapter_id") if lines else None)
    else:
        at = next((i + 1 for i, ln in enumerate(lines) if ln["id"] == body.after_line_id), None)
        if at is None:
            raise HTTPException(404, "Line not found")
        chapter = lines[at - 1].get("chapter_id")
    speakers = {c["id"] for c in s.cast or []}
    speaker = body.speaker_id if body.speaker_id in speakers else dl.NARRATOR
    if body.kind == "break":
        ln = {**dl._line(speaker, ""), "kind": "break", "pause_after_s": body.seconds or dl.BREAK_S}
    elif body.kind == "music":
        mid = sv.audio_item(db, cur.workspace_id, body.media_id or "", "music clip").id
        ln = {**dl._line(speaker, body.text or ""), "kind": "music", "media_id": mid}
    else:
        ad = None
        if body.ad_id:
            show = db.get(PodcastShow, ep.show_id) if ep.show_id else None
            ad = next((a for a in (show.ads if show else None) or [] if a.get("id") == body.ad_id), None)
            if ad is None:
                raise HTTPException(404, "That ad isn't in the show's library")
        mid = (ad or {}).get("media_id") or body.media_id
        text = (body.text or (ad or {}).get("text") or "").strip()
        if mid:
            mid = sv.audio_item(db, cur.workspace_id, mid, "ad clip").id
        elif not text:
            raise HTTPException(422, "An ad needs a clip or the words of a sponsor read")
        host = (ad or {}).get("host_id")
        if host in speakers:
            speaker = host
        ln = {**dl._line(speaker, text or (ad or {}).get("name") or "Ad"), "kind": "ad"}
        if mid:
            ln["media_id"] = mid
    if chapter:
        ln["chapter_id"] = chapter
    lines.insert(at, ln)
    s.lines = lines
    dl.refresh(db, s)
    db.commit()
    return _out(db, ep)


@router.post("/episodes/{episode_id}/mix", response_model=JobOut, status_code=202)
def mix(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    job = produce.queue_mix(db, cur, ep)
    db.commit()
    return job_out(job)


@router.post("/episodes/{episode_id}/notes", response_model=JobOut, status_code=202)
def notes(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    if not ((ep.outputs or {}).get("mix") or {}).get("timeline"):
        raise HTTPException(409, "Mix the episode first; the notes need its timings")
    return _queue(db, ep, {"op": "notes"}, "Writing the show notes")


@router.post("/episodes/{episode_id}/publish", response_model=JobOut, status_code=202)
def publish_episode(episode_id: str, body: PublishIn | None = None, db: Session = Depends(get_db),
                    cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    body = body or PublishIn()
    job = publish.queue_publish(db, cur, ep, body.format, body.cover_media_id, body.notes)
    db.commit()
    return job_out(job)


@router.post("/episodes/{episode_id}/video", response_model=JobOut, status_code=202)
def video(episode_id: str, body: VideoIn | None = None, db: Session = Depends(get_db),
          cur: CurrentUser = Depends(require_editor)):
    ep = _ep(db, cur, episode_id)
    body = body or VideoIn()
    job = publish.queue_video(db, cur, ep, body.aspect, body.burn_subtitles, body.cover_media_id)
    db.commit()
    return job_out(job)


@router.get("/episodes/{episode_id}/outputs", response_model=OutputsOut)
def outputs(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    ep = _ep(db, cur, episode_id)
    out = sv.episode_out(db, ep)
    has_mix = out["outputs"]["mix"] is not None
    return OutputsOut(**out["outputs"], notes=out["notes"],
                      srt_url=f"/api/podcast/episodes/{ep.id}/subtitles.srt" if has_mix else None,
                      notes_url=f"/api/podcast/episodes/{ep.id}/notes.md")


def _file_name(ep: PodcastEpisode) -> str:
    name = lib.short_title(ep.title or "episode", 6)
    out = "".join(ch if ch.isascii() and (ch.isalnum() or ch in " -_") else "_" for ch in name).strip()
    return out or "episode"


@router.get("/episodes/{episode_id}/subtitles.srt")
def subtitles(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    ep = _ep(db, cur, episode_id)
    tl = ((ep.outputs or {}).get("mix") or {}).get("timeline")
    if not tl:
        raise HTTPException(409, "Mix the episode first")
    return Response(publish.srt(tl), media_type="application/x-subrip; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_file_name(ep)}.srt"'})


@router.get("/episodes/{episode_id}/notes.md")
def notes_md(episode_id: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    ep = _ep(db, cur, episode_id)
    show = db.get(PodcastShow, ep.show_id) if ep.show_id else None
    chapters = ((ep.outputs or {}).get("mix") or {}).get("chapters") or []
    return Response(publish.notes_markdown(ep, show, ep.notes, chapters), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_file_name(ep)}-notes.md"'})
