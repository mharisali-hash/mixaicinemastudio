"""AI writing jobs run by the worker, plus the lock rules they share with the API.

Lock rule (PLAN 2b): AI writes straight into a field only when it is empty or the scene
is still an untouched AI draft (source 'ai', not locked). Anything else becomes a
Suggestion the user accepts or rejects.
"""
import json
import logging
import re
import time
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.llm import LLMError, chat_sync
from app.llm import prompts as P
from app.llm import film
from app.llm.client import is_warm, model_for
from app import aerab
from app import brand
from app import film_language as fl
from app import brand_moments as bm
from app import storyboard as sb
from app.models import Character, Job, Location, Project, Scene, SceneVersion, Shot, Suggestion, utcnow
from app.schemas import flush_left
from app.services import enqueue_generation

log = logging.getLogger("mixai.ai")

SCRIPT_FIELDS = ("heading", "logline", "script_text")
META_FIELDS = ("time_of_day", "mood")

PRIORITY = {
    "ai_assist": 5,
    "ai_portrait_prompt": 5,
    "ai_character_profile": 5,
    "ai_extract_characters": 3,
    "ai_write_missing": 2,
    "ai_continue": 2,
    "ai_outline": 1,
    "ai_compile_prompts": 5,
    "ai_suggest_shots": 4,
    "ai_extract_locations": 3,
    "ai_storyboard": 1,
    "ai_brand_moments": 3,
    "ai_extend_shot": 5,
    "ai_summarize": -10,
}
SUMMARY_QUIET = timedelta(seconds=8)
SUMMARY_MAX_WAIT = 30.0


# ---------------------------------------------------------------- queueing

def enqueue_ai(db: Session, *, workspace_id: str, type: str, payload: dict, project_id: str | None) -> Job:
    job = Job(
        workspace_id=workspace_id,
        type=type,
        payload=payload,
        project_id=project_id,
        priority=PRIORITY.get(type, 0),
        message="Waiting for a worker",
    )
    db.add(job)
    db.flush()
    return job


def active_job(db: Session, type: str, project_id: str, **match) -> Job | None:
    rows = db.scalars(
        select(Job).where(Job.type == type, Job.project_id == project_id, Job.status.in_(("queued", "running")))
    ).all()
    for j in rows:
        if all((j.payload or {}).get(k) == v for k, v in match.items()):
            return j
    return None


def queue_summary(db: Session, scene: Scene) -> Job | None:
    """Debounced: one queued summary job per scene; the job itself waits for typing to stop."""
    queued = db.scalars(
        select(Job).where(Job.type == "ai_summarize", Job.project_id == scene.project_id, Job.status == "queued")
    ).all()
    if any((j.payload or {}).get("scene_id") == scene.id for j in queued):
        return None
    return enqueue_ai(
        db, workspace_id=scene.workspace_id, type="ai_summarize", payload={"scene_id": scene.id}, project_id=scene.project_id
    )


# ---------------------------------------------------------------- lock rules

def ordered_scenes(db: Session, project_id: str) -> list[Scene]:
    return list(
        db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(Scene.order, Scene.created_at)).all()
    )


def renumber(scenes: list[Scene]) -> None:
    for i, s in enumerate(scenes, start=1):
        s.order = i


def snapshot(scene: Scene, user_id: str | None = None) -> SceneVersion:
    return SceneVersion(
        workspace_id=scene.workspace_id,
        scene_id=scene.id,
        version=scene.version,
        heading=scene.heading,
        logline=scene.logline,
        script_text=scene.script_text,
        source=scene.source,
        created_by=user_id,
    )


def ai_may_write(current: str | None, source: str, locked: bool) -> bool:
    return not (current or "").strip() or (source == "ai" and not locked)


def suggest(db: Session, *, target, target_type: str, field: str, proposed: str, action: str, job_id: str | None) -> Suggestion:
    # a newer suggestion for the same field replaces the old one
    db.execute(
        update(Suggestion)
        .where(
            Suggestion.target_type == target_type,
            Suggestion.target_id == target.id,
            Suggestion.field == field,
            Suggestion.status == "pending",
        )
        .values(status="rejected", resolved_at=utcnow())
    )
    s = Suggestion(
        workspace_id=target.workspace_id,
        project_id=target.project_id,
        target_type=target_type,
        target_id=target.id,
        field=field,
        current_text=getattr(target, field) or "",
        proposed_text=proposed,
        action=action,
        job_id=job_id,
    )
    db.add(s)
    db.flush()
    return s


def write_scene(db: Session, scene: Scene, values: dict, *, action: str, job_id: str | None,
                summarize: bool = True) -> dict:
    """Apply AI output to a scene under the lock rules. Returns which fields were written / suggested."""
    untouched_ai = scene.source == "ai" and not scene.locked
    had_text = any((getattr(scene, f) or "").strip() for f in SCRIPT_FIELDS)
    written, suggested = [], []

    for field in SCRIPT_FIELDS:
        new = (values.get(field) or "").strip()
        cur = getattr(scene, field) or ""
        if not new or new == cur.strip():
            continue
        if ai_may_write(cur, scene.source, scene.locked):
            setattr(scene, field, new)
            written.append(field)
        else:
            suggested.append(suggest(db, target=scene, target_type="scene", field=field, proposed=new,
                                     action=action, job_id=job_id).id)

    # chips are cheap to change back, so they never need a suggestion
    for field in META_FIELDS:
        new = values.get(field)
        if new and (not getattr(scene, field) or untouched_ai):
            setattr(scene, field, new)

    if written:
        if not had_text or untouched_ai:
            scene.source, scene.locked = "ai", False
        elif scene.source == "user":
            scene.source = "ai_edited"
        scene.version += 1
        db.add(snapshot(scene))
        if "script_text" in written:
            sb.mark_scene_shots_stale(db, scene.id)
            if summarize:
                queue_summary(db, scene)
    return {"written": written, "suggestion_ids": suggested}


def _clean_name(name: str) -> str:
    n = re.sub(r"\(.*?\)", "", name).strip(" .:-")
    return n.title() if n.isupper() else n


def upsert_character(db: Session, project: Project, name: str, description: str, *, action: str, job_id: str | None):
    name = _clean_name(name)
    if not name:
        return None, "skipped"
    description = description.strip()
    existing = next(
        (c for c in _characters(db, project.id) if c.name.casefold() == name.casefold()),
        None,
    )
    if existing is None:
        c = Character(workspace_id=project.workspace_id, project_id=project.id, name=name,
                      description=description, source="ai", locked=False)
        db.add(c)
        db.flush()
        return c, "created"
    if not description or description == existing.description.strip():
        return existing, "unchanged"
    if ai_may_write(existing.description, existing.source, existing.locked):
        existing.description = description
        if existing.source != "ai":
            existing.source = "ai_edited" if existing.locked else "ai"
        return existing, "updated"
    s = suggest(db, target=existing, target_type="character", field="description", proposed=description,
                action=action, job_id=job_id)
    return existing, s.id


def accept(db: Session, s: Suggestion, user_id: str):
    model = {"scene": Scene, "character": Character, "location": Location, "shot": Shot}.get(s.target_type)
    target = db.get(model, s.target_id) if model else None
    if target is None:
        raise LookupError(f"The {s.target_type} for this suggestion no longer exists")
    if s.field == "brand_placements":
        # chosen by the user, so it's theirs now and later AI passes only suggest
        target.brand_placements = [{**p, "source": "user"} for p in json.loads(s.proposed_text or "[]")]
        s.status = "accepted"
        s.resolved_at = utcnow()
        return target
    if s.field == "shots":
        # the user picked the AI's shot list over what was there, so it replaces the lot
        replace_shots(db, target, json.loads(s.proposed_text or "[]"), source="ai_edited", locked=True)
        s.status = "accepted"
        s.resolved_at = utcnow()
        return target
    setattr(target, s.field, s.proposed_text)
    # the user chose this text, so it is theirs now: mixed provenance, locked
    target.source = "ai_edited"
    target.locked = True
    if s.target_type == "scene":
        target.version += 1
        db.add(snapshot(target, user_id))
        if s.field == "script_text":
            sb.mark_scene_shots_stale(db, target.id)
            queue_summary(db, target)
    s.status = "accepted"
    s.resolved_at = utcnow()
    return target


# ---------------------------------------------------------------- job plumbing

class AiRun:
    """Tracks the LLM calls of one job: progress text, timings and the model's thinking."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.calls: list[dict] = []
        self.thinking: list[str] = []
        self.extra: dict = {}

    film_model: str | None = None  # set by the worker's prompt fill for an autopilot shot

    def call(self, label: str, frac: float, role, messages, **kw):
        # an autopilot film's steps run inline with its FILM_LLM_MODEL in the payload
        payload = getattr(self.ctx.job, "payload", None) or {}
        kw = {**film.overrides(self.film_model or payload.get("film_model"), role), **kw}
        if payload.get("inline_of") or self.film_model:
            kw.setdefault("think", False)  # planning, not puzzles: thinking only eats the budget
            try:
                return self._call(label, frac, role, messages, **kw)
            except LLMError as e:
                return film.retry_json(lambda m, **k: self._call(f"{label} (again)", frac, role, m, **k),
                                       messages, kw, e)
        try:
            return self._call(label, frac, role, messages, **kw)
        except LLMError as e:
            # the thinking model can think past the time limit on a long script; the writer answers straight away
            if role != "reasoning" or "didn't answer within" not in str(e) or kw.get("model"):
                raise
            log.warning("%s: %s; trying the creative model", label, e)
            return self._call(f"{label} (quicker model)", frac, "creative", messages,
                              **{k: v for k, v in kw.items() if k != "think"})

    def _call(self, label: str, frac: float, role, messages, **kw):
        cold = not is_warm(role)
        shown = (f"{label} (loading {kw.get('model') or model_for(role)}, the first call can take a few minutes)"
                 if cold else label)
        self.ctx.progress(frac, shown + "…")
        t0 = time.monotonic()

        def tick():
            self.ctx.progress(frac, f"{shown}… {int(time.monotonic() - t0)} s")

        res = chat_sync(role, messages, tick=tick, **kw)
        info = res.call_info(role)
        info["step"] = label
        self.calls.append(info)
        if res.thinking and role == "reasoning":
            self.thinking.append(res.thinking)
        log.info("%s: %s %.1fs", label, res.model, res.seconds)
        return res

    def result(self, **more) -> dict:
        out = {**self.extra, **more, "calls": self.calls}
        if self.thinking:
            out["thinking"] = "\n\n---\n\n".join(self.thinking)
        return out

    def publish(self, **more) -> None:
        # visible to the UI on the next progress commit; new dict so the JSON column notices
        self.extra.update(more)
        self.ctx.job.result = self.result()


def _owned(db: Session, model, obj_id: str | None, job: Job, label: str):
    obj = db.get(model, obj_id) if obj_id else None
    if obj is None or obj.workspace_id != job.workspace_id:
        raise RuntimeError(f"{label} no longer exists")
    return obj


def _characters(db: Session, project_id: str) -> list[Character]:
    from app.cast import project_cast

    return project_cast(db, project_id)


def _tokens_for(words: int) -> int:
    return int(words * 2.2) + 300


def _new_scene(project: Project, plan: P.OutlineScene) -> Scene:
    return Scene(
        workspace_id=project.workspace_id,
        project_id=project.id,
        heading=plan.heading.strip().upper(),
        logline=plan.logline.strip(),
        summary=plan.logline.strip(),
        time_of_day=plan.time_of_day,
        mood=plan.mood.strip()[:200],
        source="ai",
        locked=False,
        version=1,
    )


def with_aerab(progress, script: str, frac: float) -> str:
    """The verified second pass over a scene the LLM just wrote: Urdu, Arabic... lines get their marks
    checked and filled in. With the LLM away the scene stays as written."""
    if not aerab.applies(None, script):
        return script
    progress(frac, "Adding aerab to the dialogue…")
    return aerab.finish_sync(script, tick=lambda: progress(frac, "Adding aerab to the dialogue…"), what="scene").text


def _draft_into(run: AiRun, db: Session, project: Project, scenes: list[Scene], idx: int, plan: dict, frac: float,
                action: str) -> dict:
    s = scenes[idx]
    words = P.words_for(plan.get("duration_s") or 30)
    res = run.call(
        f"Writing scene {idx + 1} of {len(scenes)}: {s.heading or 'untitled'}", frac, "creative",
        P.draft_scene_messages(project, _characters(db, project.id), scenes, idx, plan=plan),
        temperature=0.8, max_tokens=_tokens_for(words),
    )
    script = with_aerab(run.ctx.progress, P.clean_script(res.content, s.heading), frac)
    # outline/continue scenes already carry the planned logline as their summary
    return write_scene(db, s, {"script_text": script}, action=action, job_id=run.ctx.job.id, summarize=False)


# ---------------------------------------------------------------- handlers

def _resume_outline(db: Session, job: Job, project: Project, run: "AiRun"):
    """A retried outline job picks up the scenes its earlier attempt created instead of starting over."""
    prev = job.result or {}
    ids = prev.get("scene_ids") or []
    by_id = {s.id: s for s in ordered_scenes(db, project.id)}
    if not ids or not prev.get("plan") or any(i not in by_id for i in ids):
        return None
    if prev.get("thinking"):
        run.thinking.append(prev["thinking"])
    run.calls.extend(prev.get("calls") or [])
    return [by_id[i] for i in ids], prev["plan"], prev.get("roles") or []


def handle_outline(ctx) -> dict:
    db, job = ctx.db, ctx.job
    project = _owned(db, Project, job.payload.get("project_id"), job, "Project")
    run = AiRun(ctx)
    resumed = _resume_outline(db, job, project, run)

    if resumed:
        scenes, plans, roles = resumed
        ctx.progress(0.15, "Picking up where the AI Director left off")
    else:
        existing = ordered_scenes(db, project.id)
        if any((s.script_text or s.heading or s.logline).strip() for s in existing):
            raise RuntimeError("This project already has scenes. Use Write missing scenes or Continue story instead.")
        for s in existing:  # empty stubs only
            db.delete(s)

        kit = bm.project_kit(db, project)
        res = run.call("The AI Director is outlining your film", 0.03, "reasoning",
                       P.outline_messages(project, brand.prompt_context(kit) if kit else ""),
                       schema=P.Outline, temperature=0.6, max_tokens=8000)
        outline: P.Outline = res.data
        if not project.logline.strip():
            project.logline = outline.logline.strip()

        scenes = [_new_scene(project, plan) for plan in outline.scenes]
        db.add_all(scenes)
        renumber(scenes)
        db.flush()
        for sc in scenes:
            db.add(snapshot(sc))
        for c in outline.characters:
            upsert_character(db, project, c.name, "", action="outline", job_id=job.id)
        plans = [pl.model_dump() for pl in outline.scenes]
        roles = [[c.name, c.role] for c in outline.characters]
        run.publish(scene_ids=[s.id for s in scenes], drafted_ids=[], plan=plans, roles=roles)

    drafted = [s.id for s in scenes if s.script_text.strip()]
    n = len(scenes)
    for i, plan in enumerate(plans):
        if scenes[i].script_text.strip():
            continue
        _draft_into(run, db, project, scenes, i, plan, 0.15 + 0.7 * i / n, "outline")
        drafted.append(scenes[i].id)
        run.publish(drafted_ids=list(drafted))

    script = "\n\n".join(f"{s.heading}\n{s.script_text}" for s in scenes)
    char_ids = []
    if roles:
        looks = run.call("Describing the cast", 0.9, "creative", P.looks_messages(project, roles, script),
                         schema=P.CastLooks, temperature=0.5, max_tokens=1500).data
        for look in looks.characters:
            c, _ = upsert_character(db, project, look.name, look.description, action="outline", job_id=job.id)
            if c:
                char_ids.append(c.id)
    project.updated_at = utcnow()
    return run.result(scene_ids=[s.id for s in scenes], drafted_ids=drafted, character_ids=char_ids)


def handle_write_missing(ctx) -> dict:
    db, job = ctx.db, ctx.job
    p = job.payload
    project = _owned(db, Project, p.get("project_id"), job, "Project")
    scenes = ordered_scenes(db, project.id)
    wanted = set(p.get("scene_ids") or [])
    targets = [s for s in scenes if not (s.script_text or "").strip() and (not wanted or s.id in wanted)]

    if not targets and p.get("after_scene_id"):
        idx = next((i for i, s in enumerate(scenes) if s.id == p["after_scene_id"]), None)
        if idx is None:
            raise RuntimeError("The scene to insert after no longer exists")
        fresh = [Scene(workspace_id=project.workspace_id, project_id=project.id, source="ai", locked=False, version=1)
                 for _ in range(int(p.get("count") or 1))]
        scenes[idx + 1:idx + 1] = fresh
        renumber(scenes)
        db.add_all(fresh)
        db.flush()
        targets = fresh
    if not targets:
        raise RuntimeError("Every scene already has a script. Add an empty scene where the gap is, then try again.")

    run = AiRun(ctx)
    run.publish(scene_ids=[t.id for t in targets], drafted_ids=[])
    suggestions, drafted = [], []
    for k, s in enumerate(targets):
        idx = scenes.index(s)
        res = run.call(f"Writing scene {idx + 1} ({k + 1} of {len(targets)})", 0.05 + 0.9 * k / len(targets),
                       "creative", P.draft_json_messages(project, _characters(db, project.id), scenes, idx),
                       schema=P.SceneDraft, temperature=0.8, max_tokens=_tokens_for(P.words_for(45)))
        d: P.SceneDraft = res.data
        script = with_aerab(ctx.progress, P.clean_script(d.script_text, d.heading), 0.05 + 0.9 * k / len(targets))
        out = write_scene(db, s, {"heading": d.heading.upper(), "logline": d.logline, "time_of_day": d.time_of_day,
                                  "mood": d.mood, "script_text": script},
                          action="write_missing", job_id=job.id)
        suggestions += out["suggestion_ids"]
        drafted.append(s.id)
        run.publish(drafted_ids=list(drafted))
    return run.result(scene_ids=[t.id for t in targets], drafted_ids=drafted, suggestion_ids=suggestions)


def handle_continue(ctx) -> dict:
    db, job = ctx.db, ctx.job
    project = _owned(db, Project, job.payload.get("project_id"), job, "Project")
    count = int(job.payload.get("count") or 1)
    scenes = ordered_scenes(db, project.id)
    if not any((s.script_text or "").strip() for s in scenes):
        raise RuntimeError("There's no story to continue yet. Write a scene or let the AI Director outline the film.")

    run = AiRun(ctx)
    plans = run.call("Planning what happens next", 0.05, "reasoning",
                     P.next_scenes_messages(project, _characters(db, project.id), scenes, count),
                     schema=P.NextScenes, temperature=0.7, max_tokens=4000).data.scenes[:count]
    fresh = [_new_scene(project, pl) for pl in plans]
    scenes += fresh
    renumber(scenes)
    db.add_all(fresh)
    db.flush()
    for sc in fresh:
        db.add(snapshot(sc))
    run.publish(scene_ids=[s.id for s in fresh], drafted_ids=[])

    drafted = []
    for k, (sc, pl) in enumerate(zip(fresh, plans)):
        _draft_into(run, db, project, scenes, scenes.index(sc), pl.model_dump(), 0.3 + 0.65 * k / len(fresh), "continue")
        drafted.append(sc.id)
        run.publish(drafted_ids=list(drafted))
    project.updated_at = utcnow()
    return run.result(scene_ids=[s.id for s in fresh], drafted_ids=drafted)


def handle_assist(ctx) -> dict:
    db, job = ctx.db, ctx.job
    p = job.payload
    scene = _owned(db, Scene, p.get("scene_id"), job, "Scene")
    project = db.get(Project, scene.project_id)
    scenes = ordered_scenes(db, project.id)
    idx = scenes.index(scene)
    chars = _characters(db, project.id)
    action = p.get("action")
    run = AiRun(ctx)

    if action == "draft_from_idea":
        d: P.SceneDraft = run.call("Drafting the scene from your idea", 0.1, "creative",
                                   P.draft_json_messages(project, chars, scenes, idx, idea=p.get("idea") or ""),
                                   schema=P.SceneDraft, temperature=0.8, max_tokens=_tokens_for(P.words_for(45))).data
        values = {"heading": d.heading.upper(), "logline": d.logline, "time_of_day": d.time_of_day, "mood": d.mood,
                  "script_text": P.clean_script(d.script_text, d.heading or scene.heading)}
    elif action == "suggest_logline":
        if not scene.script_text.strip():
            raise RuntimeError("Write or draft the scene first; the logline is taken from its script.")
        res = run.call("Writing a logline", 0.1, "creative", P.logline_messages(project, scene),
                       temperature=0.5, max_tokens=200)
        values = {"logline": P.clean_line(res.content)}
    elif action in P.ASSIST_INSTRUCTIONS:
        if not scene.script_text.strip():
            raise RuntimeError("This scene has no script yet. Use Let AI draft first.")
        words = len(scene.script_text.split())
        scale = {"expand": 2.2, "tighten": 1.0}.get(action, 1.5)
        label = {"expand": "Expanding", "tighten": "Tightening", "rewrite_tone": "Rewriting",
                 "write_dialogue": "Writing dialogue for"}[action]
        res = run.call(f"{label} the scene", 0.1, "creative",
                       P.assist_messages(project, chars, scenes, idx, action, tone=p.get("tone") or ""),
                       temperature=0.8, max_tokens=_tokens_for(int(words * scale) + 60))
        values = {"script_text": P.clean_script(res.content, scene.heading)}
    else:
        raise RuntimeError(f"Unknown AI Assist action '{action}'")

    if values.get("script_text"):
        values["script_text"] = with_aerab(ctx.progress, values["script_text"], 0.8)
    out = write_scene(db, scene, values, action=action, job_id=job.id)
    outcome = "suggested" if out["suggestion_ids"] else ("written" if out["written"] else "unchanged")
    return run.result(action=action, outcome=outcome, scene_ids=[scene.id] if out["written"] else [],
                      suggestion_ids=out["suggestion_ids"])


def handle_extract_characters(ctx) -> dict:
    db, job = ctx.db, ctx.job
    project = _owned(db, Project, job.payload.get("project_id"), job, "Project")
    scenes = [s for s in ordered_scenes(db, project.id) if s.script_text.strip()]
    if not scenes:
        raise RuntimeError("There's no script to read yet.")
    script = "\n\n".join(f"{s.heading}\n{s.script_text}" for s in scenes)
    known = [c.name for c in _characters(db, project.id)]

    run = AiRun(ctx)
    looks = run.call("Reading the script for characters", 0.1, "creative", P.extract_messages(project, script, known),
                     schema=P.CastLooks, temperature=0.4, max_tokens=2000).data
    created, updated, suggestions = [], [], []
    for look in looks.characters:
        c, outcome = upsert_character(db, project, look.name, look.description, action="extract", job_id=job.id)
        if c is None:
            continue
        if outcome == "created":
            created.append(c.id)
        elif outcome == "updated":
            updated.append(c.id)
        elif outcome not in ("unchanged", "skipped"):
            suggestions.append(outcome)
    return run.result(character_ids=created + updated, created_ids=created, updated_ids=updated,
                      suggestion_ids=suggestions)


def handle_portrait_prompt(ctx) -> dict:
    db, job = ctx.db, ctx.job
    character = _owned(db, Character, job.payload.get("character_id"), job, "Character")
    project = db.get(Project, character.project_id) if character.project_id else None
    run = AiRun(ctx)
    res = run.call(f"Writing a portrait prompt for {character.name}", 0.1, "creative",
                   P.portrait_messages(project, character), temperature=0.6, max_tokens=400)
    return run.result(character_id=character.id, prompt=P.clean_line(res.content))


def handle_character_profile(ctx) -> dict:
    """Fills only the empty profile fields, so the user's own words stay; Urdu / Arabic values get aerab.
    The suggested props, accessories and details go into the sheet's lists where those are still empty."""
    from app import character_sheet as cs

    db, job = ctx.db, ctx.job
    character = _owned(db, Character, job.payload.get("character_id"), job, "Character")
    project = db.get(Project, character.project_id) if character.project_id else None
    run = AiRun(ctx)
    film = f"{project.title}. {project.logline}".strip(" .") if project is not None else ""
    data = run.call(f"Writing {character.name}'s profile", 0.1, "creative", P.profile_messages(character, film),
                    schema=P.CharacterProfile, temperature=0.5, max_tokens=900).data
    have = dict(character.profile or {})
    filled = {k: " ".join(str(getattr(data, k) or "").split())[:300] for k in cs.PROFILE_FIELDS if not have.get(k)}
    filled = {k: v for k, v in filled.items() if v}
    if filled and aerab.applies(None, "\n".join(filled.values())):
        keys = list(filled)
        marked = aerab.finish_lines_sync([filled[k] for k in keys], None,
                                         tick=lambda: ctx.progress(0.8, "Adding aerab to the profile"),
                                         what="character profile")
        filled = dict(zip(keys, marked))
    character.profile = {**have, **filled}
    lists = dict((character.sheet or {}).get("lists") or {})
    for name in ("props", "accessories", "details"):
        if not lists.get(name):
            lists[name] = [" ".join(str(x).split())[:120] for x in getattr(data, name) if str(x).strip()]
    cs.put(character, lists=cs.clean_lists(lists))
    character.updated_at = utcnow()
    return run.result(character_id=character.id, filled=sorted(filled), profile=character.profile,
                      lists=(character.sheet or {}).get("lists"))


def handle_summarize(ctx) -> dict:
    db, job = ctx.db, ctx.job
    scene = _owned(db, Scene, job.payload.get("scene_id"), job, "Scene")
    started = time.monotonic()
    # wait for the typing to settle so we summarise the final text, not every keystroke
    while utcnow() - scene.updated_at < SUMMARY_QUIET and time.monotonic() - started < SUMMARY_MAX_WAIT:
        ctx.progress(0.05, "Waiting for edits to settle")
        time.sleep(2.0)
        db.refresh(scene)
    if not scene.script_text.strip():
        scene.summary = ""
        return {"scene_ids": [scene.id], "summary": ""}
    run = AiRun(ctx)
    res = run.call("Updating the scene summary", 0.3, "creative", P.summary_messages(scene), temperature=0.3, max_tokens=200)
    scene.summary = P.clean_line(res.content)
    return run.result(scene_ids=[scene.id], summary=scene.summary)


# ---------------------------------------------------------------- storyboard

PLAN_FIELDS = ("shot_type", "duration_s", "description", "camera", "character_ids", "location_id", "seam_in",
               "handoff_text", "start_prompt", "end_prompt", "motion_prompt")


def _text(v: str) -> str:
    return flush_left(P.clean_script(v or "")).strip()


def _char_ids(names: list[str], chars: list[Character]) -> list[str]:
    out = []
    for raw in names or []:
        n = _clean_name(raw).casefold()
        if not n:
            continue
        hit = next((c for c in chars if c.name.casefold() == n), None) or next(
            (c for c in chars if n in c.name.casefold().split() or c.name.casefold() in n), None)
        if hit and hit.id not in out:
            out.append(hit.id)
    return out


def has_dialogue(scene: Scene) -> bool:
    return any(kind == "dialogue" for kind, _, _ in sb.screenplay_lines(scene.script_text or ""))


def _dialogue_rows(ideas, chars: list[Character], cast_ids: list[str]) -> list[dict]:
    """The planner's lines as shot.dialogue rows (contract v26). A line nobody in the cast speaks (narration,
    a voice-over) can't be lip-synced and is left out; a speaker missing from the shot's cast is added."""
    from app.shot_speech import LINE_MAX, MAX_LINES

    rows = []
    for d in ideas or []:
        text = " ".join((d.text or "").split())
        ids = _char_ids([d.character], chars) if text else []
        if not ids:
            continue
        rows.append({"character_id": ids[0], "text": text[:LINE_MAX]})
        if ids[0] not in cast_ids:
            cast_ids.append(ids[0])
    return rows[:MAX_LINES]


def _mark_dialogue(run: "AiRun", plans: list[dict], frac: float, language: str | None = None) -> None:
    """The verified aerab pass over the planned lines; the planner was asked for the marks already. A film with
    its own language (contract v4a) also tags each line with it, for the voice that speaks it."""
    rows = [r for p in plans for r in p.get("dialogue") or []]
    lang = language if language and language != "en" else None
    if lang:
        for r in rows:
            r["language"] = lang
        if any(fl.off_script(r["text"], lang) for r in rows):
            # the planner wrote English for an Urdu film: TranslateGemma, then the aerab pass below
            run.ctx.progress(frac, "Translating the dialogue…")
            done = fl.localise([r["text"] for r in rows], lang, tick=lambda: run.ctx.progress(frac, "Translating…"))
            for r, text in zip(rows, done):
                r["text"] = text
    if not rows or not aerab.applies(lang, "\n".join(r["text"] for r in rows)):
        return
    run.ctx.progress(frac, "Adding aerab to the dialogue…")
    done = aerab.finish_lines_sync([r["text"] for r in rows], lang, what="shot dialogue",
                                   tick=lambda: run.ctx.progress(frac, "Adding aerab to the dialogue…"))
    for r, text in zip(rows, done):
        r["text"] = text


def _closing_id(db: Session, project_id: str) -> str | None:
    return bm.closing_record(db.get(Project, project_id)).get("shot_id")


def replace_shots(db: Session, scene: Scene, plans: list[dict], *, source: str = "ai", locked: bool = False) -> list[Shot]:
    # the advert's closing shot isn't part of any plan; it stays and moves to the end
    keep = _closing_id(db, scene.project_id)
    old = sb.scene_shots(db, scene.id)
    sb.delete_shots(db, [s for s in old if s.id != keep])
    db.flush()
    shots = []
    for i, plan in enumerate(plans, start=1):
        shot = Shot(workspace_id=scene.workspace_id, project_id=scene.project_id, scene_id=scene.id, order=i,
                    source=source, locked=locked, prompt_mode="auto",
                    **{k: plan[k] for k in PLAN_FIELDS if plan.get(k) is not None})
        shot.character_ids = list(plan.get("character_ids") or [])
        shot.brand_placements = list(plan.get("brand_placements") or [])
        shot.dialogue = list(plan.get("dialogue") or [])
        shots.append(shot)
    db.add_all(shots)
    db.flush()
    sb.renumber(shots + [s for s in old if s.id == keep])
    return shots


def apply_shot_plan(db: Session, scene: Scene, plans: list[dict], *, action: str, job_id: str | None):
    """Lock rule for shots: AI replaces a scene's shots only while they are all untouched AI drafts.
    Brand placements the user edited count as touched too."""
    keep = _closing_id(db, scene.project_id)
    existing = [s for s in sb.scene_shots(db, scene.id) if s.id != keep]
    touched = [s for s in existing
               if s.locked or s.source != "ai" or bm.user_placed(s) or sb.has_approved_work(db, s)]
    if not touched:
        return replace_shots(db, scene, plans), None
    db.execute(
        update(Suggestion)
        .where(Suggestion.target_type == "scene", Suggestion.target_id == scene.id, Suggestion.field == "shots",
               Suggestion.status == "pending")
        .values(status="rejected", resolved_at=utcnow())
    )
    sug = Suggestion(
        workspace_id=scene.workspace_id, project_id=scene.project_id, target_type="scene", target_id=scene.id,
        field="shots", action=action, job_id=job_id,
        current_text=json.dumps([{"shot_type": s.shot_type, "duration_s": s.duration_s, "description": s.description}
                                 for s in existing]),
        proposed_text=json.dumps(plans),
    )
    db.add(sug)
    db.flush()
    return [], sug.id


def _previous_scene(db: Session, scene: Scene) -> Scene | None:
    scenes = ordered_scenes(db, scene.project_id)
    i = scenes.index(scene)
    return scenes[i - 1] if i > 0 else None


def _plan_shots(run: AiRun, db: Session, project: Project, scene: Scene, max_shots: int, frac: float,
                first_seam: str = "cut", dialogue: bool = True, target_s: float | None = None) -> list[dict]:
    chars = _characters(db, project.id)
    kit = bm.project_kit(db, project)
    talk = dialogue and has_dialogue(scene)
    # a long scene's list runs to dozens of shots, each with its lines; Urdu with aerab is token-hungry
    budget = min(16000, max(5000, max_shots * (420 if P.film_language(project) != "en" else 260)))
    ideas = run.call(f"Planning shots for {scene.heading or 'the scene'}", frac, "reasoning",
                     P.suggest_shots_messages(project, chars, scene, max_shots, _previous_scene(db, scene),
                                              brand=bm.planner_brief(kit) if kit else "", dialogue=talk,
                                              target_s=target_s),
                     schema=P.ShotList, temperature=0.5, max_tokens=budget).data.shots[:max_shots]
    if target_s and len(ideas) < max_shots:
        # weak planners hand back one 60 s shot for a minute-long scene; ask once with the number spelled out
        msgs = P.suggest_shots_messages(project, chars, scene, max_shots, _previous_scene(db, scene),
                                        brand=bm.planner_brief(kit) if kit else "", dialogue=talk, target_s=target_s)
        msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] + (
            f"\n\nYour last list had {len(ideas)} shot{'s' if len(ideas) != 1 else ''}. It must have exactly "
            f"{max_shots} shots of 5-10 seconds each.")}
        try:
            again = run.call(f"Planning shots for {scene.heading or 'the scene'} again", frac, "reasoning", msgs,
                             schema=P.ShotList, temperature=0.5, max_tokens=budget).data.shots[:max_shots]
            if len(again) > len(ideas):
                ideas = again
        except LLMError as e:
            log.warning("second shot list failed, splitting the first: %s", e)
    plans = []
    for i, idea in enumerate(ideas):
        seam = first_seam if i == 0 else ("continue" if idea.continuous else "cut")
        cast_ids = _char_ids(idea.characters, chars)
        plans.append({
            "shot_type": idea.shot_type, "duration_s": idea.duration_s, "description": idea.description.strip(),
            "camera": idea.camera.strip()[:300], "location_id": scene.location_id, "seam_in": seam,
            "handoff_text": idea.handoff.strip() if i else "",
            "start_prompt": _text(idea.start_visual), "end_prompt": _text(idea.end_visual), "motion_prompt": "",
            "brand_placements": bm.placements_from(kit, idea.brand_placements) if kit else [],
            "dialogue": _dialogue_rows(idea.dialogue, chars, cast_ids) if talk else [],
            "character_ids": cast_ids,
        })
    if talk:
        # every line of the script in exactly one shot: duplicates go, dropped ones go back in order
        ensure_lines(plans, script_lines(scene, chars))
    speakers = [r["character_id"] for r in script_lines(scene, chars)]
    for p in plans:
        if not p["character_ids"]:
            # nobody named in frame: the shot's speakers, else the scene's
            p["character_ids"] = list(dict.fromkeys([r["character_id"] for r in p["dialogue"]] or speakers[:2]))
    if target_s:
        plans = split_plans(plans, max_shots)
    if talk:
        _mark_dialogue(run, plans, frac, P.film_language(project))
    return plans


# a split shot's beats: same place and moment, a different angle each
BEATS = (("wide", "wide view of the moment"), ("medium", "medium shot"), ("close_up", "close-up"),
         ("over_shoulder", "reaction, over the shoulder"), ("insert", "insert on a telling detail"))
SPLIT_MAX_S = 10.0  # one LTX pass: single takes over chunked long ones


def split_plans(plans: list[dict], need: int, cap: float = SPLIT_MAX_S, limit: int = 40) -> list[dict]:
    """Splits the longest shots into consecutive beats until there are `need` of them and none runs past `cap`
    seconds. Deterministic: the planner had its two chances."""
    out = [dict(p) for p in plans]
    k = 0
    while out and len(out) < limit:
        i = max(range(len(out)), key=lambda j: float(out[j].get("duration_s") or 0))
        if len(out) >= need and float(out[i].get("duration_s") or 0) <= cap:
            break
        a = out[i]
        shot_type, note = BEATS[k % len(BEATS)]
        k += 1
        half = round(float(a.get("duration_s") or 0) / 2, 1) or 2.0
        lines = list(a.get("dialogue") or [])
        cut = (len(lines) + 1) // 2
        b = {**a, "shot_type": shot_type, "duration_s": half, "seam_in": "cut", "handoff_text": "",
             "description": f"{a.get('description') or ''} ({note})".strip(),
             "start_prompt": a.get("end_prompt") or a.get("start_prompt") or "",
             "dialogue": lines[cut:], "character_ids": list(a.get("character_ids") or [])}
        a.update(duration_s=half, dialogue=lines[:cut])
        out.insert(i + 1, b)
    return out


def script_lines(scene: Scene, chars: list[Character]) -> list[dict]:
    """The scene's spoken lines as dialogue rows, a speech per row; lines nobody in the cast speaks are left out."""
    from app.shot_speech import LINE_MAX

    rows: list[dict] = []
    last = None
    for kind, cue, line in sb.screenplay_lines(scene.script_text or ""):
        if kind != "dialogue":
            last = last if kind == "paren" else None
            continue
        ids = _char_ids([cue], chars) if cue else []
        if not ids:
            continue
        if last is not None and last["character_id"] == ids[0]:
            last["text"] = f"{last['text']} {line}"[:LINE_MAX]
        else:
            last = {"character_id": ids[0], "text": line[:LINE_MAX]}
            rows.append(last)
    from app.film_audio import _split_line

    # a speech longer than one shot goes in at its sentence breaks, so the shots can share it
    return [{**r, "text": t} for r in rows for t in _split_line(r["text"])]


def _key(text: str) -> str:
    return " ".join(aerab.strip(text or "").split()).casefold()


def ensure_lines(plans: list[dict], rows: list[dict]) -> None:
    """Every script line in exactly one shot. The script is the source: a planner's line that isn't in it
    (reworded, or English for an Urdu film) goes, and the script's own words come back in its place."""
    seen: set[str] = set()
    script = {_key(r["text"]) for r in rows}
    for p in plans:
        keep = []
        for r in p["dialogue"]:
            k = _key(r["text"])
            if k not in seen and (k in script or not script):
                seen.add(k)
                keep.append(r)
        p["dialogue"] = keep
    missing = [r for r in rows if _key(r["text"]) not in seen]
    if missing:
        log.info("putting %d dropped line(s) back into the shots", len(missing))
        for r in missing:
            # where it falls in the script decides which shot says it
            k = rows.index(r)
            p = plans[min(len(plans) - 1, k * len(plans) // max(1, len(rows)))]
            p["dialogue"].append(dict(r))
            if r["character_id"] not in p["character_ids"]:
                p["character_ids"].append(r["character_id"])


def spread_lines(plans: list[dict], rows: list[dict]) -> None:
    from app.shot_speech import MAX_LINES

    if not plans:
        return
    for k, r in enumerate(rows):
        p = plans[min(len(plans) - 1, k * len(plans) // len(rows))]
        if len(p["dialogue"]) < MAX_LINES:
            p["dialogue"].append(dict(r))
            if r["character_id"] not in p["character_ids"]:
                p["character_ids"].append(r["character_id"])


def compile_shot(run: AiRun, db: Session, shot: Shot, frac: float, label: str | None = None) -> None:
    project = db.get(Project, shot.project_id)
    scene = db.get(Scene, shot.scene_id)
    chars = [c for c in (db.get(Character, cid) for cid in shot.character_ids or []) if c is not None]
    loc_id = shot.location_id or scene.location_id
    location = db.get(Location, loc_id) if loc_id else None
    prev, _ = sb.neighbours(db, shot)
    d: P.FramePrompts = run.call(
        label or f"Writing prompts for shot {shot.order} of {scene.heading or 'the scene'}", frac, "creative",
        P.compile_messages(project, scene, shot, chars, location, prev, sb.is_linked(shot, prev)),
        schema=P.FramePrompts, temperature=0.5, max_tokens=1600,
    ).data
    shot.start_prompt, shot.end_prompt, shot.motion_prompt = _text(d.start_prompt), _text(d.end_prompt), _text(d.motion_prompt)


def fill_shot_prompt(ctx, gen) -> str:
    """Called by the generate handler when a shot generation was queued without a prompt."""
    db = ctx.db
    shot = db.get(Shot, gen.target_id) if gen.target_type == "shot" else None
    if shot is None:
        raise RuntimeError("The shot for this generation no longer exists")
    kind = "keyframe_start" if gen.kind == "keyframe_mid" else gen.kind
    field = {"keyframe_start": "start_prompt", "keyframe_end": "end_prompt", "take": "motion_prompt"}[kind]
    if not getattr(shot, field).strip() and shot.prompt_mode == "auto":
        run = AiRun(ctx)
        run.film_model = (gen.params or {}).get("film_model")
        try:
            compile_shot(run, db, shot, 0.02, label="Writing this shot's prompts first")
        except LLMError as e:
            raise RuntimeError(f"Couldn't write this shot's prompts ({e}). Write them in the shot inspector, "
                               "or try again when the AI is reachable.") from e
        db.commit()
    prompt = sb.frame_prompt(db, shot, kind)
    if not prompt:
        prompt = sb.with_style(db.get(Project, shot.project_id), shot.prompt or shot.description)
    if not prompt.strip():
        raise RuntimeError("This shot has no prompt and no description to work from. Describe the shot first.")
    ctx.progress(0.05, "Prompt ready")
    # brand surfaces (keyframes) or the steady-camera note (takes) decided when it was queued
    return bm.with_fragment(prompt, (gen.params or {}).get("brand_prompt"))


def handle_suggest_shots(ctx) -> dict:
    db, job = ctx.db, ctx.job
    scene = _owned(db, Scene, job.payload.get("scene_id"), job, "Scene")
    if not scene.script_text.strip():
        raise RuntimeError("This scene has no script yet; shots are planned from the script.")
    project = db.get(Project, scene.project_id)
    run = AiRun(ctx)
    plans = _plan_shots(run, db, project, scene, int(job.payload.get("max_shots") or 6), 0.1)
    shots, sug = apply_shot_plan(db, scene, plans, action="suggest_shots", job_id=job.id)
    closing = bm.ensure_closing(db, project) if shots else None
    if shots:
        sb.set_review(project, scene.id, "pending" if job.payload.get("review_first") else None)
    return run.result(outcome="suggested" if sug else "written", scene_ids=[scene.id],
                      shot_ids=[s.id for s in shots], suggestion_ids=[sug] if sug else [],
                      closing_shot_id=closing.id if closing else None)


def handle_compile_prompts(ctx) -> dict:
    db, job = ctx.db, ctx.job
    shot = _owned(db, Shot, job.payload.get("shot_id"), job, "Shot")
    if shot.prompt_mode == "manual":
        return {"outcome": "unchanged", "shot_ids": [shot.id], "calls": []}
    run = AiRun(ctx)
    compile_shot(run, db, shot, 0.1)
    return run.result(outcome="written", shot_ids=[shot.id], prompt=shot.start_prompt)


def upsert_location(db: Session, project: Project, idea: P.LocationIdea, *, action: str, job_id: str | None):
    name = _clean_name(idea.name)
    if not name:
        return None, "skipped"
    if name.islower():
        name = name.title()
    desc = idea.description.strip()
    times = [t for t in (P._time(v) for v in idea.time_of_day_variants) if t]
    existing = next((loc for loc in db.scalars(select(Location).where(Location.project_id == project.id)).all()
                     if loc.name.casefold() == name.casefold()), None)
    if existing is None:
        loc = Location(workspace_id=project.workspace_id, project_id=project.id, name=name, description=desc,
                       source="ai", locked=False, time_of_day_variants=list(dict.fromkeys(times)))
        db.add(loc)
        db.flush()
        return loc, "created"
    # variants are chips, cheap to undo, so they merge without a suggestion
    merged = list(dict.fromkeys([*(existing.time_of_day_variants or []), *times]))
    if merged != list(existing.time_of_day_variants or []):
        existing.time_of_day_variants = merged
    if not desc or desc == existing.description.strip():
        return existing, "unchanged"
    if ai_may_write(existing.description, existing.source, existing.locked):
        existing.description = desc
        if existing.source != "ai":
            existing.source = "ai_edited" if existing.locked else "ai"
        return existing, "updated"
    s = suggest(db, target=existing, target_type="location", field="description", proposed=desc,
                action=action, job_id=job_id)
    return existing, s.id


def handle_extract_locations(ctx) -> dict:
    db, job = ctx.db, ctx.job
    project = _owned(db, Project, job.payload.get("project_id"), job, "Project")
    scenes = [s for s in ordered_scenes(db, project.id) if (s.heading or s.script_text).strip()]
    if not scenes:
        raise RuntimeError("There's no script to read locations from yet.")
    known = [loc.name for loc in db.scalars(select(Location).where(Location.project_id == project.id)).all()]
    run = AiRun(ctx)
    found = run.call("Reading the script for locations", 0.1, "creative",
                     P.extract_locations_messages(project, scenes, known),
                     schema=P.LocationList, temperature=0.4, max_tokens=2500).data
    created, updated, suggestions, linked = [], [], [], []
    for idea in found.locations:
        loc, outcome = upsert_location(db, project, idea, action="extract", job_id=job.id)
        if loc is None:
            continue
        if outcome == "created":
            created.append(loc.id)
        elif outcome == "updated":
            updated.append(loc.id)
        elif outcome not in ("unchanged", "skipped"):
            suggestions.append(outcome)
        for n in idea.scenes:
            # only fill gaps: a scene the user already pointed at a location keeps it
            if 1 <= n <= len(scenes) and scenes[n - 1].location_id is None:
                scenes[n - 1].location_id = loc.id
                linked.append(scenes[n - 1].id)
                for shot in sb.scene_shots(db, scenes[n - 1].id):
                    shot.location_id = shot.location_id or loc.id
    return run.result(location_ids=created + updated, created_ids=created, updated_ids=updated,
                      suggestion_ids=suggestions, scene_ids=linked)


def queue_frames(db: Session, shot: Shot) -> list:
    if bm.is_reveal(db, shot):
        return []  # the exact logo reveal has no frames; its take is already queued
    prev, _ = sb.neighbours(db, shot)
    kinds = ["keyframe_end"] if sb.is_linked(shot, prev) else ["keyframe_start", "keyframe_end"]
    gens = []
    for kind in kinds:
        prompt, params = sb.prepare_shot_generation(db, shot, kind, "", {})
        gens.append(enqueue_generation(db, workspace_id=shot.workspace_id, project_id=shot.project_id,
                                       target_type="shot", target_id=shot.id, kind=kind, prompt=prompt, params=params))
    return gens


def _scene_plan(run: AiRun, db: Session, project: Project, scene: Scene, frac: float, seam: str,
                dialogue: bool = True) -> dict:
    chars = _characters(db, project.id)
    location = db.get(Location, scene.location_id) if scene.location_id else None
    duration = sb.script_duration(scene.script_text)
    kit = bm.project_kit(db, project)
    talk = dialogue and has_dialogue(scene)
    d: P.SceneFrames = run.call(
        f"Writing first and last frames for {scene.heading or 'the scene'}", frac, "creative",
        P.scene_frames_messages(project, chars, scene, location, duration, _previous_scene(db, scene),
                                brand=bm.planner_brief(kit) if kit else "", dialogue=talk),
        schema=P.SceneFrames, temperature=0.5, max_tokens=1800,
    ).data
    cast_ids = _char_ids(d.characters, chars) or list(dict.fromkeys(
        r["character_id"] for r in script_lines(scene, chars)))[:2]
    plan = {
        "shot_type": d.shot_type, "duration_s": duration,
        "description": (d.description or scene.logline or scene.summary).strip(),
        "camera": d.camera.strip()[:300], "location_id": scene.location_id, "seam_in": seam, "handoff_text": "",
        "start_prompt": _text(d.start_prompt), "end_prompt": _text(d.end_prompt), "motion_prompt": _text(d.motion_prompt),
        "brand_placements": bm.placements_from(kit, d.brand_placements) if kit else [],
        "dialogue": _dialogue_rows(d.dialogue, chars, cast_ids) if talk else [],
        "character_ids": cast_ids,
    }
    if talk:
        _mark_dialogue(run, [plan], frac, P.film_language(project))
    return plan


def handle_storyboard(ctx) -> dict:
    db, job = ctx.db, ctx.job
    p = job.payload
    project = _owned(db, Project, p.get("project_id"), job, "Project")
    wanted = set(p.get("scene_ids") or [])
    targets = [s for s in ordered_scenes(db, project.id)
               if s.script_text.strip() and (not wanted or s.id in wanted)]
    if not targets:
        raise RuntimeError("There's no script to storyboard yet.")
    mode, chain = p.get("mode") or "scene", bool(p.get("chain"))
    review = bool(p.get("review_first"))
    # the review gate holds frames back until the user approves the shot list
    make_frames = p.get("generate_frames", True) and not review
    run = AiRun(ctx)
    done: dict[str, list] = {"scene_ids": [], "skipped_scene_ids": [], "shot_ids": [], "suggestion_ids": [],
                             "generation_ids": [], "frame_job_ids": []}

    keep = _closing_id(db, project.id)
    for k, scene in enumerate(targets):
        frac = 0.05 + 0.9 * k / len(targets)
        if [s for s in sb.scene_shots(db, scene.id) if s.id != keep] and not p.get("overwrite"):
            done["skipped_scene_ids"].append(scene.id)
            continue
        seam = "continue" if chain else "cut"
        # contract v26: the planner hands each shot its spoken lines unless the film has no dialogue
        talk = p.get("dialogue", True) is not False
        if mode == "shots":
            # the autopilot's long scenes (contract v4a) each say how many shots and seconds they need
            many = int((p.get("max_shots_by_scene") or {}).get(scene.id) or p.get("max_shots") or 6)
            plans = _plan_shots(run, db, project, scene, many, frac, first_seam=seam, dialogue=talk,
                                target_s=(p.get("scene_seconds") or {}).get(scene.id))
        else:
            plans = [_scene_plan(run, db, project, scene, frac, seam, dialogue=talk)]
        shots, sug = apply_shot_plan(db, scene, plans, action="storyboard", job_id=job.id)
        if sug:
            done["suggestion_ids"].append(sug)
            continue
        prev, _ = sb.neighbours(db, shots[0])
        if prev is None:
            shots[0].seam_in = "cut"  # nothing before the film's first shot to continue from
        if mode == "shots":
            for i, shot in enumerate(shots):
                compile_shot(run, db, shot, frac + 0.9 / len(targets) * (i + 1) / (len(shots) + 1))
        db.flush()
        sb.set_review(project, scene.id, "pending" if review else None)
        if make_frames:
            for shot in shots:
                for g in queue_frames(db, shot):
                    done["generation_ids"].append(g.id)
                    done["frame_job_ids"].append(g.job_id)
        done["scene_ids"].append(scene.id)
        done["shot_ids"] += [s.id for s in shots]
        run.publish(**done)
        ctx.progress(frac, f"Storyboarded {len(done['scene_ids'])} of {len(targets)} scenes")
    if done["shot_ids"]:
        had = bm.closing_record(project).get("shot_id")
        closing = bm.ensure_closing(db, project)
        if closing is not None:
            done["closing_shot_id"] = closing.id
            if closing.id != had and make_frames:
                for g in queue_frames(db, closing):
                    done["generation_ids"].append(g.id)
                    done["frame_job_ids"].append(g.job_id)
    project.updated_at = utcnow()
    return run.result(outcome="suggested" if done["suggestion_ids"] and not done["shot_ids"] else "written",
                      review_scene_ids=done["scene_ids"] if review else [], **done)


def handle_extend_shot(ctx) -> dict:
    """Writes the beat for a shot made by POST /shots/{id}/extend; the user's words are kept (lock rule)."""
    db, job = ctx.db, ctx.job
    shot = _owned(db, Shot, job.payload.get("shot_id"), job, "The new shot")
    before = db.get(Shot, job.payload.get("from_shot_id") or "")
    if before is None:
        raise RuntimeError("The shot being extended no longer exists")
    project = db.get(Project, shot.project_id)
    scene = db.get(Scene, shot.scene_id)
    chars = [c for c in (db.get(Character, cid) for cid in shot.character_ids or []) if c is not None]
    loc_id = shot.location_id or scene.location_id
    run = AiRun(ctx)
    d: P.NextBeat = run.call("Writing the next beat", 0.1, "creative",
                             P.extend_messages(project, scene, before, chars, db.get(Location, loc_id) if loc_id else None,
                                               shot.duration_s, job.payload.get("hint") or ""),
                             schema=P.NextBeat, temperature=0.7, max_tokens=1200).data
    if ai_may_write(shot.description, shot.source, shot.locked):
        shot.description = d.description.strip()
    if not shot.camera.strip() or not shot.locked:
        shot.camera = d.camera.strip()[:300] or shot.camera
    if shot.prompt_mode == "auto":
        shot.end_prompt, shot.motion_prompt = _text(d.end_prompt), _text(d.motion_prompt)
    return run.result(outcome="written", shot_ids=[shot.id], scene_ids=[scene.id])


def handle_brand_moments(ctx) -> dict:
    db, job = ctx.db, ctx.job
    project = _owned(db, Project, job.payload.get("project_id"), job, "Project")
    run = AiRun(ctx)
    out = bm.plan_existing(run, db, project, job.id)
    project.updated_at = utcnow()
    return run.result(outcome="suggested" if out["suggestion_ids"] and not out["shot_ids"] else "written", **out)


AI_HANDLERS = {
    "ai_suggest_shots": handle_suggest_shots,
    "ai_compile_prompts": handle_compile_prompts,
    "ai_extract_locations": handle_extract_locations,
    "ai_storyboard": handle_storyboard,
    "ai_brand_moments": handle_brand_moments,
    "ai_extend_shot": handle_extend_shot,
    "ai_outline": handle_outline,
    "ai_write_missing": handle_write_missing,
    "ai_continue": handle_continue,
    "ai_assist": handle_assist,
    "ai_extract_characters": handle_extract_characters,
    "ai_portrait_prompt": handle_portrait_prompt,
    "ai_character_profile": handle_character_profile,
    "ai_summarize": handle_summarize,
}


# ---------------------------------------------------------------- regenerate with note

def rewrite_prompt_from_note(prompt: str, note: str, timeout: float = 60.0) -> tuple[str, str | None]:
    """Creative model folds the note into the prompt. Falls back to appending, with a reason to show."""
    try:
        res = chat_sync("creative", P.rewrite_prompt_messages(prompt, note), temperature=0.4, max_tokens=500,
                        timeout=timeout)
        new = P.clean_line(res.content)
        if len(new) >= 10:
            return new, None
        reason = "the AI's rewrite came back empty"
    except LLMError as e:
        reason = str(e)
    log.warning("prompt rewrite failed, appending the note instead: %s", reason)
    return f"{prompt.rstrip()}\n\nNote: {note}", reason
