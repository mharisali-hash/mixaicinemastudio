"""Read-only report of what has been going wrong with video work, to paste back for a fix.

    cd backend && uv run python ../scripts/video-failures.py [--days 7] [--all-lanes]

Groups failed jobs by type, workflow and error (numbers and ids blanked, so the same fault counts once), lists
work that is stuck or paused, and shows the workers and the GPU queues. Changes nothing.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Generation, Job, WorkerHeartbeat  # noqa: E402

# orchestrators that end in a video even though they run on the general lane
VIDEO_PARENTS = ("presenter", "poetry", "story", "podcast", "autopilot", "film", "montage", "assemble", "dub",
                 "shot", "take", "lipsync", "upscale", "clip", "extend")


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def shape(err: str | None) -> str:
    line = next((ln.strip() for ln in (err or "no error text").splitlines() if ln.strip()), "no error text")
    line = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", line)
    line = re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", line)
    line = re.sub(r"\d+(\.\d+)?", "N", line)
    return line[:220]


def last_lines(err: str | None, n: int = 6) -> str:
    lines = [ln for ln in (err or "").splitlines() if ln.strip()]
    return "\n      ".join(lines[-n:]) if lines else "(none)"


def is_video(j: Job, g: Generation | None) -> bool:
    if j.lane == "video":
        return True
    if g is not None and (g.kind in ("video", "take", "clip") or (g.media_type or "").startswith("video")):
        return True
    return any(k in (j.type or "") for k in VIDEO_PARENTS)


def template_of(g: Generation | None) -> str:
    if g is None:
        return "-"
    p = g.params or {}
    return str(p.get("template") or p.get("model") or p.get("lipsync_engine") or g.kind)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--all-lanes", action="store_true", help="every failure, not only video work")
    a = ap.parse_args()
    since = now() - timedelta(days=a.days)

    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.created_at >= since)))
        gens = {g.id: g for g in db.scalars(select(Generation).where(
            Generation.id.in_([j.generation_id for j in jobs if j.generation_id])))}

        def wanted(j: Job) -> bool:
            return a.all_lanes or is_video(j, gens.get(j.generation_id or ""))

        mine = [j for j in jobs if wanted(j)]
        print(f"== last {a.days:g} days: {len(mine)} {'' if a.all_lanes else 'video '}jobs")
        print("   by status:", dict(Counter(j.status for j in mine)))
        print("   by type and status:")
        for (t, s), n in sorted(Counter((j.type, j.status) for j in mine).items()):
            print(f"     {t:28} {s:10} {n}")

        failed = [j for j in mine if j.status == "failed"]
        groups: dict[tuple, list[Job]] = defaultdict(list)
        for j in failed:
            groups[(j.type, template_of(gens.get(j.generation_id or "")), shape(j.error))].append(j)
        print(f"\n== {len(failed)} failed, {len(groups)} kinds of fault (most common first)")
        for (t, tpl, err), rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            rows.sort(key=lambda j: j.created_at, reverse=True)
            users = len({j.user_id for j in rows})
            print(f"\n-- {len(rows)}x  type={t}  workflow={tpl}  people={users}  latest={rows[0].created_at:%m-%d %H:%M}")
            print(f"   error: {err}")
            print(f"   ids: {', '.join(j.id[:8] for j in rows[:5])}")
            print(f"   tail of the newest:\n      {last_lines(rows[0].error)}")

        t = now()
        print("\n== stuck or paused right now")
        live = list(db.scalars(select(Job).where(Job.status.in_(("queued", "running")))))
        for j in sorted(live, key=lambda j: j.created_at):
            if not (a.all_lanes or is_video(j, db.get(Generation, j.generation_id or ""))):
                continue
            age = (t - j.created_at).total_seconds() / 60
            beat = f"{(t - j.heartbeat_at).total_seconds():.0f}s ago" if j.heartbeat_at else "never"
            flag = ""
            if j.status == "running" and (not j.heartbeat_at or (t - j.heartbeat_at).total_seconds() > 90):
                flag = "  <-- running but silent"
            elif j.status == "queued" and age > 60:
                flag = "  <-- waiting over an hour"
            print(f"   {j.id[:8]} {j.type:24} {j.status:8} lane={j.lane:7} prio={j.priority:4} tries={j.attempts} "
                  f"age={age:.0f}m beat={beat} msg={(j.message or '')[:80]!r}{flag}")

        # orchestrators keep their stages in job.result; a failed stage leaves the rest waiting
        print("\n== stages that failed (the rest of that video waits behind them)")
        seen = 0
        for j in mine:
            for st in ((j.result or {}).get("stages") or []) if isinstance(j.result, dict) else []:
                if isinstance(st, dict) and st.get("status") == "failed":
                    seen += 1
                    print(f"   {j.id[:8]} {j.type:20} stage={st.get('key')}: {shape(st.get('detail'))}")
        if not seen:
            print("   none recorded in job results")

        print("\n== workers")
        for hb in db.scalars(select(WorkerHeartbeat)):
            info = hb.info or {}
            print(f"   {hb.id[:24]:24} last_seen={(t - hb.last_seen).total_seconds():.0f}s ago lanes={info.get('lanes')} "
                  f"comfy={info.get('comfy')}")


if __name__ == "__main__":
    main()
