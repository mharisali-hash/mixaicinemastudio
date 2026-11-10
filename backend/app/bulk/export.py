"""A batch's results as ZIP parts: the pictures, a caption .txt beside each (the layout kohya_ss and
ai-toolkit read), and metadata.jsonl / captions.csv with what was asked and what the model was told.

Parts stop at PART_FILES pictures or PART_BYTES, whichever comes first, so a 5,000-picture batch downloads
as a few files a browser and a flaky connection can cope with rather than one 20 GB archive.
"""
import csv
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bulk import service as bs
from app.config import get_settings
from app.models import BulkBatch, BulkItem, Generation, Job, MediaItem

PART_FILES = 1000
PART_BYTES = 2 * 1024**3
EXPORT_PRIORITY = -10
COLUMNS = ("file", "original_file", "original_filename", "action", "instruction", "prompt_used", "keywords", "caption", "width",
           "height", "seed", "model", "sha256", "batch", "finished_at")


def queue(db: Session, batch: BulkBatch, *, originals: bool, captions: bool) -> Job:
    job = Job(workspace_id=batch.workspace_id, type=bs.EXPORT_JOB, priority=EXPORT_PRIORITY,
              user_id=batch.user_id, message="Waiting for a worker",
              payload={"batch_id": batch.id, "originals": originals, "captions": captions})
    db.add(job)
    db.flush()
    batch.exports = [*(batch.exports or []), job.id][-10:]
    return job


def exports_dir(workspace_id: str) -> Path:
    return get_settings().data_dir / "workspaces" / workspace_id / "exports"


def _caption(instruction: str, keywords: str) -> str:
    return ", ".join(x for x in (instruction, keywords) if x)


def _row(batch: BulkBatch, item: BulkItem, g: Generation, arc: str) -> dict:
    p = g.params or {}
    instr = bs.instruction_for(batch, item)
    kw = bs.keywords_for(batch, item)
    size = p.get("size") or [p.get("width"), p.get("height")]
    return {"file": arc, "original_filename": item.filename, "action": batch.action, "instruction": instr,
            "prompt_used": item.prompt_used or "", "keywords": kw, "caption": _caption(instr, kw),
            "width": size[0] if size else None, "height": size[1] if size and len(size) > 1 else None,
            "seed": g.seed, "model": p.get("model_resolved") or p.get("model") or
            ((p.get("restore") or {}).get("template") if batch.action == "cutout" else None) or
            (p.get("image_tool") or {}).get("template"),
            "sha256": item.sha256, "batch": batch.name,
            "finished_at": item.finished_at.isoformat() + "Z" if item.finished_at else None}


def _unique(stem: str, ext: str, used: set[str]) -> str:
    name, n = f"{stem}{ext}", 2
    while name.lower() in used:
        name, n = f"{stem}-{n}{ext}", n + 1
    used.add(name.lower())
    return name


class _Part:
    def __init__(self, out_dir: Path, job_id: str, n: int):
        self.final = out_dir / f"bulk-{job_id}-{n}.zip"
        self.tmp = self.final.with_suffix(".zip.part")
        self.zf = zipfile.ZipFile(self.tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True)
        self.rows: list[dict] = []
        self.bytes = 0

    def close(self) -> dict:
        jsonl = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self.rows)
        self.zf.writestr("metadata.jsonl", jsonl.encode("utf-8"), zipfile.ZIP_DEFLATED)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(self.rows)
        # with a BOM so Excel opens Urdu and Arabic prompts as UTF-8
        self.zf.writestr("captions.csv", ("﻿" + buf.getvalue()).encode("utf-8"), zipfile.ZIP_DEFLATED)
        self.zf.close()
        self.tmp.replace(self.final)
        root = get_settings().data_dir
        return {"file": self.final.relative_to(root).as_posix(), "count": len(self.rows),
                "bytes": self.final.stat().st_size}


def handle(ctx) -> dict:
    db, job = ctx.db, ctx.job
    pl = job.payload or {}
    batch = db.get(BulkBatch, pl.get("batch_id") or "")
    if batch is None:
        raise RuntimeError("The batch has been deleted")
    items = db.scalars(select(BulkItem).where(BulkItem.batch_id == batch.id).order_by(BulkItem.seq)).all()
    done = [i for i in items if i.status == "done" and i.result_gen_id]
    failed = [i for i in items if i.status in ("failed", "cancelled")]
    if not done:
        raise RuntimeError("No finished pictures to export yet")
    out_dir = exports_dir(batch.workspace_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    bs.check_disk()
    root = get_settings().data_dir
    parts: list[dict] = []
    part = _Part(out_dir, job.id, 1)
    used: set[str] = set()
    for n, item in enumerate(done):
        g = db.get(Generation, item.result_gen_id)
        src = (root / g.file_path) if g is not None and g.file_path else None
        if src is None or not src.is_file():
            failed.append(item)
            continue
        extra = 0
        orig = None
        if pl.get("originals"):
            og = db.get(Generation, item.source_gen_id or "")
            media = db.get(MediaItem, item.media_id or "")
            # a RAW / HEIC upload: the file as it came, not the JPEG working copy
            rel = (media.source_path if media is not None and media.source_path else None) or (og.file_path if og else None)
            orig = root / rel if rel else None
            extra = orig.stat().st_size if orig is not None and orig.is_file() else 0
        size = src.stat().st_size + extra
        if part.rows and (len(part.rows) >= PART_FILES or part.bytes + size > PART_BYTES):
            parts.append(part.close())
            part = _Part(out_dir, job.id, len(parts) + 1)
            used = set()
        rel_dir = PurePosixPath(item.filename).parent
        stem = (rel_dir / PurePosixPath(item.filename).stem).as_posix().lstrip("./") or "picture"
        arc = "images/" + _unique(stem, src.suffix.lower(), used)
        part.zf.write(src, arc)
        row = _row(batch, item, g, arc)
        if pl.get("captions", True):
            part.zf.writestr(arc.rsplit(".", 1)[0] + ".txt", row["caption"].encode("utf-8"))
        if orig is not None and orig.is_file():
            o_arc = "originals/" + arc[len("images/"):].rsplit(".", 1)[0] + orig.suffix.lower()
            part.zf.write(orig, o_arc)
            row["original_file"] = o_arc
        part.rows.append(row)
        part.bytes += size
        if n % 20 == 0:
            ctx.progress((n + 1) / len(done) * 0.98, f"Zipping {n + 1} of {len(done)}")
    if failed:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(("original_filename", "status", "error"))
        w.writerows((i.filename, i.status, i.error or "") for i in failed)
        part.zf.writestr("failed.csv", ("﻿" + buf.getvalue()).encode("utf-8"), zipfile.ZIP_DEFLATED)
    parts.append(part.close())
    return {"batch_id": batch.id, "parts": parts, "count": sum(p["count"] for p in parts), "failed": len(failed)}


def part_path(job: Job, n: int) -> Path | None:
    parts = (job.result or {}).get("parts") or []
    if not 1 <= n <= len(parts):
        return None
    p = get_settings().data_dir / parts[n - 1]["file"]
    return p if p.is_file() else None


def remove_exports(db: Session, batch: BulkBatch) -> None:
    for jid in batch.exports or []:
        job = db.get(Job, jid)
        for p in ((job.result or {}).get("parts") or []) if job is not None else []:
            (get_settings().data_dir / p["file"]).unlink(missing_ok=True)
