"""Shots, locations and the rules that tie frames together (PLAN 2c).

The routers, the worker and the AI jobs all go through here so seam linking,
reference picking and staleness behave the same no matter who triggers them.
"""
import re

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import camera, models_catalog
from app.config import get_settings
from app.models import Character, Generation, Location, Project, Scene, Shot, utcnow
from app.schemas import LocationOut, ShotOut
from app.services import gen_out
from app.workflows import frames_for, snap_multiple

KEYFRAME_KINDS = ("keyframe_start", "keyframe_end", "keyframe_mid")
SHOT_KINDS = (*KEYFRAME_KINDS, "take")
LOCATION_KINDS = ("establishing",)
SHOT_TYPES = ("wide", "medium", "close_up", "extreme_close_up", "over_shoulder", "pov", "insert", "establishing",
              "long_take")

FPS = 24.0
# contract sizes before the /32 snap; LTX latents need multiples of 32
DRAFT_VIDEO = {"16:9": (768, 432), "9:16": (432, 768), "1:1": (576, 576), "2.39:1": (1024, 428), "4:3": (640, 480)}
MAX_TAKE_FRAMES = 257  # single-segment LTX; longer shots wait for the long-take templates
MAX_REFS = 3


# ---------------------------------------------------------------- ordering

def scene_shots(db: Session, scene_id: str) -> list[Shot]:
    return list(db.scalars(select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order, Shot.created_at)).all())


def project_shots(db: Session, project_id: str) -> list[Shot]:
    q = (
        select(Shot)
        .join(Scene, Scene.id == Shot.scene_id)
        .where(Shot.project_id == project_id)
        .order_by(Scene.order, Scene.created_at, Shot.order, Shot.created_at)
    )
    return list(db.scalars(q).all())


def renumber(shots: list[Shot]) -> None:
    for i, s in enumerate(shots, start=1):
        s.order = i


def neighbours(db: Session, shot: Shot) -> tuple[Shot | None, Shot | None]:
    """Previous and next shot in film order; seams cross scene boundaries."""
    shots = project_shots(db, shot.project_id)
    idx = next((i for i, s in enumerate(shots) if s.id == shot.id), None)
    if idx is None:
        return None, None
    return (shots[idx - 1] if idx > 0 else None), (shots[idx + 1] if idx + 1 < len(shots) else None)


def is_linked(shot: Shot, prev: Shot | None) -> bool:
    # a Continue seam on the very first shot has nothing to continue from, so it acts as a cut
    return shot.seam_in == "continue" and prev is not None


# ---------------------------------------------------------------- generations

def _gens_for(db: Session, target_type: str, ids: list[str], kinds) -> dict[str, list[Generation]]:
    out: dict[str, list[Generation]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = db.scalars(
        select(Generation)
        .where(Generation.target_type == target_type, Generation.target_id.in_(ids), Generation.kind.in_(kinds))
        .order_by(Generation.created_at.desc())
    ).all()
    for g in rows:
        out[g.target_id].append(g)
    return out


def current(gens: list[Generation], kind: str) -> Generation | None:
    """Approved version, else the newest finished one (gens newest first)."""
    mine = [g for g in gens if g.kind == kind]
    return next((g for g in mine if g.status == "approved"), None) or next(
        (g for g in mine if g.status == "ready"), None
    )


def approved(db: Session, target_type: str, target_id: str, kind: str) -> Generation | None:
    return db.scalars(
        select(Generation).where(
            Generation.target_type == target_type, Generation.target_id == target_id,
            Generation.kind == kind, Generation.status == "approved",
        )
    ).first()


def derive_status(start_ok: bool, end_ok: bool, takes: list[Generation]) -> str:
    if any(t.status == "approved" for t in takes):
        return "approved"
    if any(t.status in ("queued", "generating") for t in takes):
        return "rendering"
    if any(t.status == "ready" for t in takes):
        return "take_ready"
    if start_ok and end_ok:
        return "frames_ready"
    return "draft"


def shots_out(db: Session, shots: list[Shot]) -> list[ShotOut]:
    if not shots:
        return []
    order: dict[str, list[Shot]] = {}
    prev_of: dict[str, Shot | None] = {}
    for pid in {s.project_id for s in shots}:
        order[pid] = project_shots(db, pid)
        for i, s in enumerate(order[pid]):
            prev_of[s.id] = order[pid][i - 1] if i > 0 else None
    wanted = {s.id for s in shots} | {p.id for p in prev_of.values() if p is not None}
    gens = _gens_for(db, "shot", list(wanted), SHOT_KINDS)
    closing = {}
    for pid in order:
        rec = ((_project(db, pid).settings or {}).get("brand_closing_shot") or {})
        if rec.get("shot_id"):
            closing[rec["shot_id"]] = rec.get("mode")

    from app import shot_speech

    voices: dict = {}
    out = []
    for s in shots:
        mine = gens.get(s.id, [])
        prev = prev_of.get(s.id)
        o = ShotOut.model_validate(s)
        o.character_ids = list(s.character_ids or [])
        o.brand_closing = o.closing = closing.get(s.id)
        o.brand_placements_locked = any(p.get("source") == "user" for p in s.brand_placements or [])
        end = current(mine, "keyframe_end")
        if is_linked(s, prev):
            linked = current(gens.get(prev.id, []), "keyframe_end")
            o.start_frame = gen_out(linked) if linked else None
            o.start_linked = True
            start_ok = bool(linked and linked.status == "approved")
        else:
            start = current(mine, "keyframe_start")
            o.start_frame = gen_out(start) if start else None
            start_ok = bool(start and start.status == "approved")
        o.end_frame = gen_out(end) if end else None
        takes = [g for g in mine if g.kind == "take" and g.status != "rejected"]
        take = next((t for t in takes if t.status == "approved"), None)
        o.approved_take = gen_out(take) if take else None
        o.takes_count = len(takes)
        o.status = derive_status(start_ok, bool(end and end.status == "approved"), takes)
        o.activity = activity(mine)
        o.dialogue = shot_speech.public(s.dialogue)
        o.dialogue_notice = shot_speech.notice_for(db, s, voices) if s.dialogue else None
        out.append(o)
    return out


def activity(gens: list[Generation]) -> list[dict]:
    """What the shot card's status pill needs: generations in flight, plus a kind whose newest try failed."""
    out, newest = [], {}
    for g in gens:  # newest first
        newest.setdefault(g.kind, g)
        if g.status in ("queued", "generating"):
            out.append({"id": g.id, "kind": g.kind, "status": g.status, "job_id": g.job_id, "version": g.version})
    out += [{"id": g.id, "kind": k, "status": "failed", "job_id": g.job_id, "version": g.version}
            for k, g in newest.items() if g.status == "failed"]
    return out


def shot_out(db: Session, shot: Shot) -> ShotOut:
    return shots_out(db, [shot])[0]


def location_out(db: Session, loc: Location) -> LocationOut:
    o = LocationOut.model_validate(loc)
    o.time_of_day_variants = list(loc.time_of_day_variants or [])
    g = approved(db, "location", loc.id, "establishing")
    o.approved_establishing = gen_out(g) if g else None
    return o


def shot_count(db: Session, project_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Shot).where(Shot.project_id == project_id)) or 0


def delete_shots(db: Session, shots: list[Shot]) -> None:
    ids = [s.id for s in shots]
    if not ids:
        return
    db.execute(delete(Generation).where(Generation.target_type == "shot", Generation.target_id.in_(ids)))
    for s in shots:
        db.delete(s)


def has_approved_work(db: Session, shot: Shot) -> bool:
    """Approved frames or takes count as the user's work, same as a lock."""
    return bool(db.scalar(
        select(func.count()).select_from(Generation).where(
            Generation.target_type == "shot", Generation.target_id == shot.id, Generation.status == "approved"
        )
    ))


# ---------------------------------------------------------------- staleness

def has_gens(db: Session, shot_id: str, kinds) -> bool:
    return bool(db.scalar(
        select(func.count()).select_from(Generation).where(
            Generation.target_type == "shot", Generation.target_id == shot_id,
            Generation.kind.in_(kinds), Generation.status != "rejected",
        )
    ))


def mark_scene_shots_stale(db: Session, scene_id: str) -> int:
    shots = scene_shots(db, scene_id)
    for s in shots:
        s.stale = True
    return len(shots)


def after_approve(db: Session, g: Generation) -> None:
    if g.target_type != "shot" or g.kind not in ("keyframe_start", "keyframe_end"):
        return
    shot = db.get(Shot, g.target_id)
    if shot is None:
        return
    now = utcnow()
    if has_gens(db, shot.id, ("take",)):
        shot.stale = True
    shot.updated_at = now
    if g.kind == "keyframe_end":
        _, nxt = neighbours(db, shot)
        if nxt is not None and nxt.seam_in == "continue":
            # its START frame just changed under it
            if has_gens(db, nxt.id, SHOT_KINDS):
                nxt.stale = True
            nxt.updated_at = now


# ---------------------------------------------------------------- generation inputs

def _project(db: Session, project_id: str) -> Project:
    return db.get(Project, project_id)


def with_style(project: Project, text: str) -> str:
    style = (project.style_bible or "").strip()
    text = text.strip()
    if style and style not in text:
        return f"{text}\n{style}" if text else style
    return text


def shot_text(shot: Shot) -> str:
    return " ".join(x for x in (shot.description, shot.prompt, shot.start_prompt, shot.camera) if x)


def character_ref(db: Session, shot: Shot, c: Character) -> dict:
    """The picture a shot uses for one character: the user's panel pick, else the sheet panel that fits the
    framing (app.character_panels.pick_for_shot), else the approved portrait."""
    from app import character_panels as cp
    from app.character_sheet import panel_generation

    picked = (shot.ref_panels or {}).get(c.id)
    if picked and picked.startswith("look:"):
        # one of the character's looks: that outfit and setting, the same face
        from app.character_looks import finished_picture

        g = finished_picture(db, c, picked)
        if g is not None:
            return {"generation": g, "panel": picked, "auto": False}
        picked = None
    wanted = [picked] if picked else cp.pick_for_shot(shot_text(shot), shot.shot_type, shot.camera_rack)
    for key in wanted:
        if key == "portrait":
            break
        g = panel_generation(db, c.id, key)
        if g is not None:
            return {"generation": g, "panel": key, "auto": not picked}
    g = approved(db, "character", c.id, "portrait")
    if g is None:
        # a film made before sheets: an approved podcast-style sheet view still beats nothing
        g = approved(db, "character", c.id, "sheet_view")
    return {"generation": g if g is not None and g.file_path else None, "panel": "portrait" if g else None,
            "auto": not picked}


def references_for(db: Session, shot: Shot) -> tuple[list[str], list[str]]:
    """Per character the sheet panel that fits the shot (or the approved portrait), then the location's
    establishing frame. At most MAX_REFS."""
    ids, labels = [], []
    for cid in shot.character_ids or []:
        c = db.get(Character, cid)
        if c is None:
            continue
        g = character_ref(db, shot, c)["generation"]
        if g is not None:
            ids.append(g.id)
            labels.append(c.name)
    loc_id = shot.location_id or (db.get(Scene, shot.scene_id).location_id if shot.scene_id else None)
    if loc_id:
        loc = db.get(Location, loc_id)
        g = approved(db, "location", loc_id, "establishing") if loc else None
        if g and g.file_path:
            ids.append(g.id)
            labels.append(f"the location ({loc.name})")
    return ids[:MAX_REFS], labels[:MAX_REFS]


def draft_size(aspect: str) -> tuple[int, int]:
    w, h = DRAFT_VIDEO.get(aspect, DRAFT_VIDEO["16:9"])
    return snap_multiple(w, 32), snap_multiple(h, 32)


def take_frames(db: Session, shot: Shot) -> tuple[Generation | None, Generation | None]:
    prev, _ = neighbours(db, shot)
    if is_linked(shot, prev):
        start = approved(db, "shot", prev.id, "keyframe_end")
    else:
        start = approved(db, "shot", shot.id, "keyframe_start")
    return start, approved(db, "shot", shot.id, "keyframe_end")


def people_anchor(db: Session, shot: Shot) -> str:
    """Who is in frame, described in full: a video model sees each prompt alone."""
    rows = []
    for cid in shot.character_ids or []:
        c = db.get(Character, cid)
        if c is None:
            continue
        prof = c.profile or {}
        extra = "; ".join(str(prof[k]) for k in ("age", "wardrobe") if prof.get(k))
        desc = "; ".join(x for x in ((c.description or "").strip(), extra) if x)
        rows.append(f"{c.name}: {desc}" if desc else c.name)
    return ("In frame: " + " | ".join(rows) + ".") if rows else ""


def world_anchors(db: Session, shot: Shot) -> str:
    scene = db.get(Scene, shot.scene_id)
    parts = [people_anchor(db, shot)] if shot.character_ids else []
    parts = [p for p in parts if p]
    loc_id = shot.location_id or (scene.location_id if scene else None)
    loc = db.get(Location, loc_id) if loc_id else None
    if loc:
        parts.append(f"Setting: {loc.name}. {loc.description}".strip())
    if scene:
        light = ", ".join(x for x in ((scene.time_of_day or "").replace("_", " "), scene.lighting, scene.mood) if x)
        if light:
            parts.append(f"Light and mood: {light}.")
    return " ".join(parts)


def frame_prompt(db: Session, shot: Shot, kind: str) -> str:
    project = _project(db, shot.project_id)
    if kind == "take":
        base = shot.motion_prompt.strip()
        if not base:
            return ""
        # polish P2: the rack's camera sentence rides on the motion prompt
        move = camera.to_prompt(shot.camera_rack or None)
        if move and move.rstrip(".").lower() not in base.lower():
            base = camera.compose(base, shot.camera_rack)
        anchors = world_anchors(db, shot)
        return with_style(project, f"{base}\n{anchors}" if anchors and anchors not in base else base)
    base = (shot.start_prompt if kind == "keyframe_start" else shot.end_prompt).strip()
    people = people_anchor(db, shot) if base else ""
    if people and any((c.description or "").strip() and c.description.strip()[:40] not in base
                      for c in (db.get(Character, cid) for cid in shot.character_ids or []) if c is not None):
        base = f"{base}\n{people}"  # a frame written from names only (a voice-first cut) gets the looks
    return with_style(project, base) if base else ""


LINKED_DETAIL = "linked to previous shot's end frame"


def prepare_shot_generation(db: Session, shot: Shot, kind: str, prompt: str, params: dict) -> tuple[str, dict]:
    """Fill server-side defaults for a shot generation. Raises HTTPException (also fine inside jobs)."""
    if kind not in SHOT_KINDS:
        raise HTTPException(422, f"A shot can't have a '{kind}' generation")
    project = _project(db, shot.project_id)
    params = dict(params or {})
    params.setdefault("aspect_ratio", project.aspect_ratio)
    prev, _ = neighbours(db, shot)
    from app import brand_moments as bm  # imports this module

    if bm.is_reveal(db, shot):
        raise HTTPException(409, "This is the advert's exact logo reveal: it has no frames, and its take is "
                                 "made from the brand kit's logo file")

    if kind in KEYFRAME_KINDS:
        if kind == "keyframe_start" and is_linked(shot, prev):
            raise HTTPException(409, LINKED_DETAIL)
        if "reference_ids" not in params:
            ids, labels = references_for(db, shot)
            # a shot with brand placements uses the edit model with the logo/product as references
            if not bm.keyframe_refs(db, shot, project, params, ids, labels) and ids:
                params["reference_ids"], params["reference_labels"] = ids, labels
        prompt = bm.with_fragment(prompt.strip() or frame_prompt(db, shot, kind), params.get("brand_prompt"))
        models_catalog.apply_image_choice(params, project.settings)
    else:
        start, end = take_frames(db, shot)
        if start is None:
            raise HTTPException(409, "Approve a START frame first" + (
                " (this shot continues from the previous shot's END frame, which isn't approved yet)"
                if is_linked(shot, prev) else ""))
        duration = float(params.get("duration_s") or shot.duration_s or 4)
        w, h = draft_size(params["aspect_ratio"])
        params.update(
            first_frame_id=start.id,
            last_frame_id=end.id if end else None,
            duration_s=duration,
            fps=FPS,
            num_frames=min(frames_for(duration, FPS), MAX_TAKE_FRAMES),
        )
        params.setdefault("width", w)
        params.setdefault("height", h)
        from app import longtake  # imports this module

        if not 1 <= duration <= get_settings().longtake_max_s:
            raise HTTPException(422, f"duration_s must be between 1 and {get_settings().longtake_max_s:g} seconds")
        if longtake.is_long(duration):
            longtake.init_params(params)
        else:
            for k in ("longtake", "chunks", "assembly", "continuity", "context_frames", "longtake_stats"):
                params.pop(k, None)
        models_catalog.apply_take_quality(params, project.settings, long_take=longtake.is_long(duration))
        from app import shot_speech

        # the shot's lines in its characters' voices (contract v26); the worker speaks them before the take
        shot_speech.attach(db, shot, params)
        from app import film_audio

        # a shot cut from its scene's dialogue track takes its slice of that track instead
        film_audio.attach(db, shot, params)
        steady = bm.hero_motion(db, shot, project)
        if steady:
            params["brand_prompt"] = steady
        else:
            params.pop("brand_prompt", None)
        prompt = bm.with_fragment(prompt.strip() or frame_prompt(db, shot, "take"), steady)
        if params.get("lipsync"):
            # a shaking camera costs the lip-sync models the mouth; the documentary look still asks for handheld
            prompt = re.sub(r"\bhandheld\b", "tripod-mounted", prompt, flags=re.I)

    if not prompt:
        # the worker compiles start/end/motion prompts before rendering
        params["compile_first"] = True
    else:
        params.pop("compile_first", None)
    return prompt, params


def prepare_location_generation(db: Session, loc: Location, kind: str, prompt: str, params: dict) -> tuple[str, dict]:
    if kind not in LOCATION_KINDS:
        raise HTTPException(422, f"A location can't have a '{kind}' generation")
    project = _project(db, loc.project_id)
    params = dict(params or {})
    params.setdefault("aspect_ratio", project.aspect_ratio)
    models_catalog.apply_image_choice(params, project.settings)
    prompt = prompt.strip() or (
        f"Establishing shot of {loc.name}. {loc.description}".strip() + " No people, wide angle, cinematic."
    )
    return with_style(project, prompt), params


# ---------------------------------------------------------------- script timing

_CUE = re.compile(r"^[A-Z][A-Z0-9 .'\-]{1,40}(\s*\([^)]*\))?$")


def cue_name(line: str) -> str:
    # MAYA (V.O.) and MAYA (CONT'D) are both Maya
    return re.sub(r"\s*\([^)]*\)\s*$", "", line).strip()


def screenplay_lines(text: str):
    """Walk a flush-left script (llm.prompts SCRIPT_RULES): yields (kind, cue, line) where kind is
    cue | paren | dialogue | action | break. `cue` is the speaking character while in a dialogue block."""
    cue = None
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            cue = None
            yield "break", None, ""
            continue
        if _CUE.match(line) and len(line.split()) <= 5:
            cue = cue_name(line)
            yield "cue", cue, line
            continue
        if line.startswith("(") and line.endswith(")"):
            yield "paren", cue, line
            continue
        yield ("dialogue" if cue is not None else "action"), cue, line


def script_duration(text: str, lo: float = 2.0, hi: float = 20.0) -> float:
    """Screen time from a flush-left script: dialogue at ~150 wpm, action lines a bit faster."""
    dialogue = action = 0
    for kind, _, line in screenplay_lines(text):
        if kind == "dialogue":
            dialogue += len(line.split())
        elif kind == "action":
            action += len(line.split())
    seconds = dialogue / 2.5 + action / 3.5
    return max(lo, min(hi, round(seconds * 2) / 2))


# ---------------------------------------------------------------- shot-list review gate (P3)

REVIEW_KEY = "shots_review"


def set_review(project: Project, scene_id: str, state: str | None) -> None:
    s = dict(project.settings or {})
    rec = dict(s.get(REVIEW_KEY) or {})
    if state:
        rec[scene_id] = state
    else:
        rec.pop(scene_id, None)
    if rec:
        s[REVIEW_KEY] = rec
    else:
        s.pop(REVIEW_KEY, None)
    project.settings = s


def link_map(db: Session, project_id: str) -> dict[str, str | None]:
    """shot id -> id of the shot before it in film order; snapshot it before reshuffling shots."""
    shots = project_shots(db, project_id)
    return {s.id: (shots[i - 1].id if i else None) for i, s in enumerate(shots)}


def restale_links(db: Session, project_id: str, before: dict[str, str | None]) -> list[str]:
    """A Continue shot whose previous shot changed now opens on a different END frame: flag it if it has work."""
    flagged = []
    db.flush()
    for sid, prev in link_map(db, project_id).items():
        if sid not in before or before[sid] == prev:
            continue
        shot = db.get(Shot, sid)
        if shot.seam_in == "continue" and has_gens(db, sid, SHOT_KINDS):
            shot.stale = True
            flagged.append(sid)
    return flagged


def _user_edit(shots: list[Shot]) -> tuple[str, bool]:
    # merge/split rewrite the description, so the PATCH lock rule applies: the result is the user's
    return ("ai_edited" if all(s.source in ("ai", "ai_edited") for s in shots) else "user"), True


def _union(lists) -> list:
    out = []
    for items in lists:
        for x in items or []:
            if x not in out:
                out.append(x)
    return out


def merge_placements(shots: list[Shot]) -> list[dict]:
    """Union by asset and surface; a hero placement beats a background one, the user's beats the AI's."""
    out: dict[tuple, dict] = {}
    for s in shots:
        for p in s.brand_placements or []:
            key = (p.get("asset_id"), (p.get("surface") or "").casefold())
            have = out.get(key)
            if have is None:
                # older PATCHes stored placements without the defaults filled in
                out[key] = {**p, "prominence": p.get("prominence") or "background", "source": p.get("source") or "user"}
                continue
            if p.get("prominence") == "hero":
                have["prominence"] = "hero"
            if p.get("source") == "user":
                have["source"] = "user"
    return list(out.values())


def _guard_work(db: Session, shots: list[Shot], what: str) -> None:
    from app import brand_moments as bm  # imports this module

    for s in shots:
        if bm.closing_of(db, s):
            raise HTTPException(409, f"The advert's closing shot can't be {what}")
        if has_approved_work(db, s):
            raise HTTPException(409, f"Shot {s.order} has approved frames or takes; {what} it would throw them away. "
                                     "Unapprove them first.")


def merge_shots(db: Session, shots: list[Shot]) -> Shot:
    """Adjacent shots of one scene become the first of them; the others (and their drafts) go."""
    from app import longtake

    scene_ids = {s.scene_id for s in shots}
    if len(scene_ids) != 1:
        raise HTTPException(422, "Only shots of the same scene can be merged")
    scene_list = scene_shots(db, shots[0].scene_id)
    idx = sorted(scene_list.index(s) for s in shots)
    if idx != list(range(idx[0], idx[0] + len(idx))):
        raise HTTPException(422, "Merge needs neighbouring shots; reorder them first")
    _guard_work(db, shots, "merged")
    ordered = [scene_list[i] for i in idx]
    keep, rest = ordered[0], ordered[1:]
    before = link_map(db, keep.project_id)

    cap = get_settings().longtake_max_s
    total = min(cap, sum(s.duration_s for s in ordered))
    cameras = [s.camera.strip() for s in ordered if s.camera.strip()]
    racks = [dict(s.camera_rack or {}) for s in ordered]
    keep.description = " ".join(s.description.strip() for s in ordered if s.description.strip())
    keep.camera = (cameras[0] if len(set(cameras)) <= 1 else "; ".join(dict.fromkeys(cameras)))[:300] if cameras else ""
    keep.duration_s = total
    keep.camera_rack = racks[0] if all(r == racks[0] for r in racks) else {}
    if longtake.is_long(total):
        keep.shot_type = "long_take"
    keep.character_ids = _union(s.character_ids for s in ordered)
    keep.brand_placements = merge_placements(ordered)
    keep.end_prompt = ordered[-1].end_prompt
    keep.motion_prompt = ""
    keep.beats = []
    keep.source, keep.locked = _user_edit(ordered)
    if has_gens(db, keep.id, SHOT_KINDS):
        keep.stale = True
    delete_shots(db, rest)
    db.flush()
    renumber(scene_shots(db, keep.scene_id))
    restale_links(db, keep.project_id, before)
    return keep


def _split_text(text: str, ratio: float) -> tuple[str, str]:
    text = text.strip()
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if x]
    parts = sentences if len(sentences) > 1 else text.split()
    if len(parts) < 2:
        return text, text
    cut = min(len(parts) - 1, max(1, round(len(parts) * ratio)))
    return " ".join(parts[:cut]), " ".join(parts[cut:])


def split_shot(db: Session, shot: Shot, ratio: float = 0.5, descriptions: list[str] | None = None) -> Shot:
    """Two shots from one: the second continues straight on from the first (Continue seam)."""
    from app import longtake

    _guard_work(db, [shot], "split")
    if shot.duration_s < 2:
        raise HTTPException(422, "A shot under 2 seconds is too short to split")
    first_s = max(1.0, min(shot.duration_s - 1, round(shot.duration_s * ratio * 2) / 2))
    second_s = round((shot.duration_s - first_s) * 2) / 2
    if descriptions:
        a_text, b_text = (d.strip() for d in descriptions)
    else:
        a_text, b_text = _split_text(shot.description, ratio)
    before = link_map(db, shot.project_id)
    source, locked = _user_edit([shot])

    second = Shot(
        workspace_id=shot.workspace_id, project_id=shot.project_id, scene_id=shot.scene_id,
        shot_type="long_take" if longtake.is_long(second_s) else
        (shot.shot_type if shot.shot_type != "long_take" else "medium"),
        duration_s=second_s, description=b_text, camera=shot.camera, prompt=shot.prompt,
        prompt_mode=shot.prompt_mode, start_prompt="", end_prompt=shot.end_prompt, motion_prompt="",
        character_ids=list(shot.character_ids or []), location_id=shot.location_id, seam_in="continue",
        # TODO: give each half only the placements its text mentions; both halves get all of them for now
        handoff_text="", brand_placements=[dict(p) for p in shot.brand_placements or []],
        camera_rack=dict(shot.camera_rack or {}),
        source=source, locked=locked,
    )
    shot.duration_s, shot.description = first_s, a_text
    if shot.shot_type == "long_take" and not longtake.is_long(first_s):
        shot.shot_type = "medium"
    shot.end_prompt = shot.motion_prompt = ""
    shot.beats = []
    shot.source, shot.locked = source, locked
    if has_gens(db, shot.id, SHOT_KINDS):
        shot.stale = True
    insert_after(db, shot, second)
    restale_links(db, shot.project_id, before)
    return second


def insert_after(db: Session, shot: Shot, new: Shot) -> None:
    siblings = scene_shots(db, shot.scene_id)
    siblings.insert(siblings.index(shot) + 1, new)
    db.add(new)
    renumber(siblings)
    db.flush()
