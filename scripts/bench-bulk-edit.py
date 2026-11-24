"""Time the Admin Bulk edit again on pictures already on the server, to compare speed before/after a tuning change.

    cd backend && uv run python ../scripts/bench-bulk-edit.py [--n 20] [--batch "bulk 11"] [--keep]
        [--model qwen_image_edit_2511|qwen_image_edit_2509_int4] [--cards one|both] [--steps 4|8]
        [--scheduler simple|beta]

It takes N source pictures from a bulk batch (the newest edit batch by default; it needn't have run yet), queues
the batch's edit on them exactly the way Admin Bulk does (same model, size, instruction, low priority so people's requests
still go first), waits, and prints seconds per picture measured on the GPU (start to finish of each job, not
queue time) plus pictures per hour over the whole run. The test pictures go to the hidden bulk workspace and are
deleted afterwards unless --keep.

A fair A/B: same --n and --batch, one setting changed per run, nothing else on the GPUs. For example
    --cards one                      today's bulk speed on the image GPU alone
    --cards both                     half the pictures on the video GPU too (it must be idle: no takes queued)
    --cards one --scheduler beta     the sampler's scheduler for qwen_edit (default simple)
    --cards one --steps 8            8 steps instead of Lightning's 4
    --model qwen_image_edit_2509_int4 --cards one    the Nunchaku INT4 model against the fp8 default
--steps / --scheduler go on these test jobs only; the app's own edits keep the template's defaults.
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


def pct(xs: list[float], p: float) -> float:
    return xs[min(len(xs) - 1, max(0, round(p * len(xs)) - 1))]


def video_busy(db) -> int:
    return sum(1 for j in db.scalars(select(Job).where(Job.lane == "video", Job.status.in_(("queued", "running"))))
               if not (j.payload or {}).get("bench"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--batch", default="", help="batch name; default: the newest edit batch with finished pictures")
    ap.add_argument("--keep", action="store_true", help="keep the test pictures")
    ap.add_argument("--cards", choices=("one", "both"), default="one",
                    help="one: every picture on the image GPU; both: every other one on the video GPU")
    ap.add_argument("--steps", type=int, default=0, help="sampler steps (default: the template's, 4 for qwen_edit)")
    ap.add_argument("--scheduler", default="", help="sampler scheduler, e.g. simple or beta (default: simple)")
    ap.add_argument("--model", default="qwen_image_edit_2511",
                    help="edit model id, e.g. qwen_image_edit_2509_int4 (default: the production fp8 one)")
    a = ap.parse_args()

    with SessionLocal() as db:
        if a.cards == "both" and (n := video_busy(db)):
            sys.exit(f"{n} job(s) queued or running on the video GPU; wait for them so the timing is fair")
        q = select(BulkBatch).where(BulkBatch.action == "edit").order_by(BulkBatch.created_at.desc())
        if a.batch:
            q = q.where(BulkBatch.name == a.batch)
        # only the uploaded source matters, so a batch that was just uploaded works too
        has_src = BulkItem.source_gen_id.is_not(None)
        batch = next((b for b in db.scalars(q) if db.scalar(
            select(BulkItem.id).where(BulkItem.batch_id == b.id, has_src).limit(1))), None)
        if batch is None:
            sys.exit(f"no edit batch{' named ' + repr(a.batch) if a.batch else ''} with uploaded pictures found")
        items = list(db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id, has_src)
                                .order_by(BulkItem.seq).limit(a.n)))
        opts = batch.options or {}
        tuning = {**({"steps": a.steps} if a.steps else {}), **({"scheduler": a.scheduler} if a.scheduler else {})}
        jobs, made, model = [], [], None
        for item in items:
            source = db.get(Generation, item.source_gen_id or "")
            text = bs.instruction_for(batch, item)
            if source is None or not text:
                continue
            # the model is the bench's choice, not the batch's, so fp8 and int4 run on the same pictures
            model, chosen = mc.choose(a.model, "edit", text, refs=1)
            if opts.get("aspect"):
                w, h = lib.size_for_aspect(opts["aspect"])
            else:
                sw, sh = lib._image_dims(source)
                w, h = lib.size_for_ratio(sw, sh) if sw and sh else lib.size_for_aspect("1:1")
            params = {"width": w, "height": h, "aspect": opts.get("aspect") or None, "instruction": text,
                      "edit_of": [{"media_id": item.media_id, "generation_id": source.id}],
                      "reference_ids": [source.id], "model": model.id, "user_prompt": text,
                      "created_by": {"user_id": batch.user_id, "flow": "bench"}, **chosen, **tuning}
            out = MediaItem(id=new_id(), workspace_id=batch.workspace_id, kind="image", origin="generated",
                            title=f"bench · {PurePosixPath(item.filename).stem}"[:300], tags=["bench"], width=w, height=h)
            db.add(out)
            db.flush()
            g = enqueue_generation(db, workspace_id=batch.workspace_id, project_id=None, target_type="media",
                                   target_id=out.id, kind="image", prompt=text, params=params, seed=opts.get("seed"))
            out.generation_id = g.id
            job = db.get(Job, g.job_id)
            job.priority, job.user_id = bs.BULK_PRIORITY, batch.user_id
            job.payload = {**(job.payload or {}), "bench": True}
            if a.cards == "both" and len(jobs) % 2:
                job.lane = "video"
            jobs.append(job.id)
            made.append(out.id)
            db.commit()
        if not jobs:
            sys.exit("nothing to queue")
        if tuning and model.template not in ("qwen_edit", "qwen_edit_int4"):
            print(f"note: --steps/--scheduler are wired for the qwen_edit graphs; {model.template} may ignore them")
        print(f"batch '{batch.name}': queued {len(jobs)} edits with model {model.id} ({model.template}), "
              f"cards={a.cards}, {tuning or 'template defaults'}; waiting…")

    deadline = time.monotonic() + 4 * 3600
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            rows = [db.get(Job, j) for j in jobs]
            ended = [r for r in rows if r.status in ("done", "failed", "cancelled")]
            print(f"\r  {len(ended)}/{len(rows)} finished", end="", flush=True)
            if len(ended) == len(rows):
                ok = [r for r in rows if r.status == "done" and r.started_at and r.finished_at]
                secs = sorted((r.finished_at - r.started_at).total_seconds() for r in ok)
                gpu = sorted(r.gpu_seconds for r in ok if r.gpu_seconds)
                by_lane = {lane: sum(1 for r in ok if r.lane == lane) for lane in ("image", "video")}
                span = ((max(r.finished_at for r in ok) - min(r.started_at for r in ok)).total_seconds()
                        if ok else 0.0)
                failed = [r.error for r in rows if r.status != "done"]
                break
        time.sleep(10)
    else:
        sys.exit("\ngave up waiting after 4 hours")

    print()
    if secs:
        print(f"done={len(secs)} (image GPU {by_lane['image']}, video GPU {by_lane['video']})")
        print(f"per picture: median={median(secs):.1f}s  p90={pct(secs, 0.9):.1f}s  fastest={secs[0]:.1f}s"
              f"  slowest={secs[-1]:.1f}s")
        if gpu:
            print(f"ComfyUI time: median={median(gpu):.1f}s  p90={pct(gpu, 0.9):.1f}s"
                  f"   -> outside ComfyUI (checks, saving): ~{median(secs) - median(gpu):.1f}s a picture")
        # the whole run, first start to last finish: what two cards actually buy
        if span > 0:
            print(f"wall={span:.0f}s  pictures/hour={len(secs) / span * 3600:.0f}")
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
