import asyncio
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.lanes import LANES
from app.models import Generation, Job, WorkerHeartbeat, utcnow
from app.schemas import _iso
from app.security import CurrentUser, get_current_user, is_admin, require_admin, session_only

router = APIRouter(tags=["system"])

PROBE_TIMEOUT = 4.0
WORKER_ALIVE_WINDOW = timedelta(seconds=30)


@router.get("/health")
def health():
    return {"status": "ok"}


def _comfy_headers(s: Settings) -> dict:
    return {"Authorization": f"Bearer {s.comfy_auth_token}"} if s.comfy_auth_token else {}


async def _probe_comfy(client: httpx.AsyncClient, url: str, s: Settings) -> dict:
    try:
        r = await client.get(f"{url}/system_stats", headers=_comfy_headers(s))
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # any failure just means "not reachable" for the status pill
        return {"ok": False, "url": url, "error": str(e) or type(e).__name__}
    system = data.get("system", {})
    devices = [
        {"name": d.get("name", ""), "vram_total": d.get("vram_total"), "vram_free": d.get("vram_free")}
        for d in data.get("devices", [])
    ]
    return {"ok": True, "url": url, "version": system.get("comfyui_version"), "devices": devices}


async def _probe_llm(client: httpx.AsyncClient, s: Settings) -> dict:
    base = s.llm_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        r = await client.get(f"{base}/api/tags")
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
    except Exception as e:
        return {"ok": False, "url": base, "error": str(e) or type(e).__name__}
    return {"ok": True, "url": base, "models": models}


def _public(probe: dict) -> dict:
    """What a non-admin may see of a probe: up or down and the GPUs, never the internal URL or error text."""
    out = {"ok": probe.get("ok", False), "url": ""}
    for k in ("version", "devices", "models"):
        if k in probe:
            out[k] = probe[k]
    if not out["ok"]:
        out["error"] = "Not reachable"
    return out


@router.get("/system/status")
async def system_status(db: Session = Depends(get_db), cur: CurrentUser = Depends(get_current_user)):
    s = get_settings()
    urls = s.all_comfy_urls()
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, verify=s.comfy_verify_tls) as comfy_client, \
            httpx.AsyncClient(timeout=PROBE_TIMEOUT) as llm_client:
        comfy_results, llm = await asyncio.gather(
            asyncio.gather(*[_probe_comfy(comfy_client, u, s) for u in urls]),
            _probe_llm(llm_client, s),
        )
    probes = dict(zip(urls, comfy_results))

    # contract has a single comfy object; report the first healthy instance (per-GPU detail is in lanes)
    if comfy_results:
        comfy = next((c for c in comfy_results if c["ok"]), comfy_results[0])
    else:
        comfy = {"ok": False, "url": "", "error": "COMFY_URLS is empty"}

    last = db.scalar(select(func.max(WorkerHeartbeat.last_seen)))
    worker = {"alive": bool(last and utcnow() - last <= WORKER_ALIVE_WINDOW)}
    if last:
        worker["last_heartbeat"] = _iso(last)

    counts = dict(db.execute(select(Job.status, func.count()).where(Job.status.in_(("queued", "running")))
                             .group_by(Job.status)).all())
    # everyone shares the GPUs, so the queue length is useful to all; whose jobs they are isn't
    queue = {"queued": int(counts.get("queued", 0)), "running": int(counts.get("running", 0))}
    admin = is_admin(cur)
    lanes = _lanes(db, s, probes, None if admin else cur.workspace_id)
    if not admin:
        comfy, llm = _public(comfy), _public(llm)
    return {"comfy": comfy, "llm": llm, "driver": s.gen_driver, "worker": worker, "queue": queue, "lanes": lanes}


def _lanes(db: Session, s: Settings, probes: dict, workspace_id: str | None = None) -> dict:
    """workspace_id set: a non-admin's view, with only their own running jobs and no worker ids or URLs."""
    from app.worker import heartbeat_lanes

    cutoff = utcnow() - WORKER_ALIVE_WINDOW
    live = [hb for hb in db.scalars(select(WorkerHeartbeat)).all() if hb.last_seen >= cutoff]
    q = select(Job).where(Job.status == "running")
    if workspace_id:
        q = q.where(Job.workspace_id == workspace_id)
    running = db.scalars(q.order_by(Job.started_at)).all()
    out = {}
    for lane in LANES:
        mine = [hb for hb in live if lane in heartbeat_lanes(hb)]
        job = next((j for j in running if j.lane == lane and not (j.payload or {}).get("inline_of")), None)
        entry = {
            "worker": {"alive": bool(mine), "count": len(mine), "ids": [hb.id for hb in mine]},
            "job": {"id": job.id, "type": job.type, "progress": job.progress, "message": job.message}
            if job else None,
            "comfy": None,
        }
        if lane != "general":
            # audio renders on whichever GPU worker claims it, so it can use either ComfyUI
            lane_urls = (list(dict.fromkeys(s.comfy_urls_for("image") + s.comfy_urls_for("video")))
                         if lane == "audio" else s.comfy_urls_for(lane))
            healthy = next((probes[u] for u in lane_urls if probes.get(u, {}).get("ok")), None)
            # the driver takes the first healthy URL, so that's the GPU this lane is really using
            pick = healthy or (probes.get(lane_urls[0]) if lane_urls else None)
            entry["comfy"] = {"urls": lane_urls, "ok": bool(healthy), "url": (pick or {}).get("url", ""),
                              **({"devices": pick.get("devices", [])} if healthy else
                                 {"error": (pick or {}).get("error", "no ComfyUI URL for this lane")})}
            if workspace_id:
                entry["comfy"] = _public(entry["comfy"])
        if workspace_id:
            entry["worker"].pop("ids")
        out[lane] = entry
    return out


@router.get("/system/comfy-check")
async def comfy_check(_=Depends(require_admin)):
    """Validate every workflow template against the live server's /object_info."""
    from app.drivers.comfy_client import pick_client
    from app.workflows import check_template, list_templates

    s = get_settings()
    try:
        client = await pick_client(s.all_comfy_urls(), s.comfy_auth_token, s.comfy_verify_tls)
    except Exception as e:
        return {"ok": False, "url": "", "error": str(e), "templates": {}}
    try:
        info = await client.object_info()
    except Exception as e:
        return {"ok": False, "url": client.base_url, "error": f"object_info failed: {e}", "templates": {}}
    finally:
        await client.close()
    templates = {name: check_template(name, info) for name in list_templates()}
    return {"ok": all(t["ok"] for t in templates.values()), "url": client.base_url, "templates": templates}


@router.get("/system/comfy-services")
def comfy_services(_=Depends(require_admin)):
    from app import comfy_services as cs

    return {"units": cs.units()}


@router.post("/system/comfy-services/{unit}/restart")
@session_only
def comfy_restart(unit: str, db: Session = Depends(get_db), cur: CurrentUser = Depends(require_admin)):
    from app import audit
    from app import comfy_services as cs

    try:
        cs.restart(unit)
    except cs.RestartError as e:
        audit.record(db, cur.user, "comfy_restart_failed", "service", unit, error=str(e)[:300])
        db.commit()
        raise HTTPException(409, str(e)) from None
    audit.record(db, cur.user, "comfy_restart", "service", unit)
    db.commit()
    return {"unit": unit, "restarted": True}


@router.get("/system/upscale-options")
async def upscale_options(generation_id: str | None = None, db: Session = Depends(get_db),
                          cur: CurrentUser = Depends(get_current_user)):
    """Engines with availability from the template checks; with generation_id, sizes and GPU estimates
    for that video (or image) at each target."""
    from app import upscale, upscale_image

    g = None
    if generation_id:
        g = db.get(Generation, generation_id)
        if g is None or g.workspace_id != cur.workspace_id:
            raise HTTPException(404, "Generation not found")
    s = get_settings()
    info, error = (None, None) if s.gen_driver == "mock" else await upscale.fetch_object_info()
    try:
        if g is not None and upscale_image.is_image(g):
            return upscale_image.options_for(g, info, driver=s.gen_driver, error=error)
        measured = upscale.measured_rates(db)
        out = upscale.engine_options(info, driver=s.gen_driver, measured=measured, error=error)
        out["media"] = "video"
        if g is not None:
            out.update(upscale.source_estimates(g, measured))
    except upscale.UpscaleError as e:
        raise HTTPException(422, str(e)) from None
    return out
