"""Time the Admin Bulk edit again on pictures already on the server, to compare speed before/after a tuning change.

    cd backend && uv run python ../scripts/bench-bulk-edit.py [--n 20] [--batch "bulk 11"] [--keep]

It takes N finished pictures from a past bulk batch (the newest edit batch by default), queues the same edit on
their sources exactly the way Admin Bulk does (same model, size, instruction, low priority so people's requests
still go first), waits, and prints seconds per picture measured on the GPU (start to finish of each job, not
queue time). The test pictures go to the hidden bulk workspace and are deleted afterwards unless --keep.
"""
import argparse
import sys
import time
from pathlib import Path, PurePosixPath
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app import library as lib  # noqa: E402
from app import models_catalog as mc  # noqa: E402
from app.bulk import service as bs  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import BulkBatch, BulkItem, Generation, Job, MediaItem, new_id  # noqa: E402
from app.services import enqueue_generation  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--batch", default="", help="batch name; default: the newest edit batch with finished pictures")
    ap.add_argument("--keep", action="store_true", help="keep the test pictures")
    a = ap.parse_args()

    with SessionLocal() as db:
        q = select(BulkBatch).where(BulkBatch.action == "edit").order_by(BulkBatch.created_at.desc())
        if a.batch:
            q = q.where(BulkBatch.name == a.batch)
        batch = next((b for b in db.scalars(q) if db.scalar(
            select(BulkItem.id).where(BulkItem.batch_id == b.id, BulkItem.status == "done").limit(1))), None)
        if batch is None:
            sys.exit("no edit batch with finished pictures found")
        items = list(db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id, BulkItem.status == "done")
                                .order_by(BulkItem.seq).limit(a.n)))
        opts = batch.options or {}
        jobs, made = [], []
        for item in items:
            source = db.get(Generation, item.source_gen_id or "")
            text = bs.instruction_for(batch, item)
            if source is None or not text:
                continue
            model, chosen = mc.choose(opts.get("model") or None, "edit", text, refs=1)
            if opts.get("aspect"):
                w, h = lib.size_for_aspect(opts["aspect"])
            else:
                sw, sh = lib._image_dims(source)
                w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
            params = {"width": w, "height": h, "aspect": opts.get("aspect") or None, "instruction": text,
                      "edit_of": [{"media_id": item.media_id, "generation_id": source.id}],
                      "reference_ids": [source.id], "model": model.id, "user_prompt": text,
                      "created_by": {"user_id": batch.user_id, "flow": "bench"}, **chosen}
            out = MediaItem(id=new_id(), workspace_id=batch.workspace_id, kind="image", origin="generated",
                            title=f"bench · {PurePosixPath(item.filename).stem}"[:300], tags=["bench"], width=w, height=h)
            db.add(out)
            db.flush()
            g = enqueue_generation(db, workspace_id=batch.workspace_id, project_id=None, target_type="media",
                                   target_id=out.id, kind="image", prompt=text, params=params, seed=opts.get("seed"))
            out.generation_id = g.id
            job = db.get(Job, g.job_id)
            job.priority, job.user_id = bs.BULK_PRIORITY, batch.user_id
            jobs.append(job.id)
            made.append(out.id)
            db.commit()
        print(f"batch '{batch.name}': queued {len(jobs)} edits with model {model.id}; waiting…")

    deadline = time.monotonic() + 4 * 3600
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            rows = [db.get(Job, j) for j in jobs]
            ended = [r for r in rows if r.status in ("done", "failed", "cancelled")]
            print(f"\r  {len(ended)}/{len(rows)} finished", end="", flush=True)
            if len(ended) == len(rows):
                secs = sorted((r.finished_at - r.started_at).total_seconds() for r in rows
                              if r.status == "done" and r.started_at and r.finished_at)
                failed = [r.error for r in rows if r.status != "done"]
                break
        time.sleep(10)
    else:
        sys.exit("\ngave up waiting after 4 hours")

    print()
    if secs:
        print(f"done={len(secs)}  median={median(secs):.1f}s  p90={secs[int(len(secs) * 0.9) - 1 if len(secs) > 1 else 0]:.1f}s"
              f"  fastest={secs[0]:.1f}s  slowest={secs[-1]:.1f}s")
    for e in failed[:5]:
        print("failed:", (e or "")[:200])
    if not a.keep:
        with SessionLocal() as db:
            for mid in made:
                m = db.get(MediaItem, mid)
                if m is not None:
                    lib.delete_item(db, m)
            db.commit()
        print("test pictures deleted (--keep to keep them)")


if __name__ == "__main__":
    main()
