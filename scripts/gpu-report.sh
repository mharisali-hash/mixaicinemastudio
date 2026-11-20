#!/usr/bin/env bash
# Read-only snapshot of the GPU box for tuning: cards, power, ComfyUI launch flags, attention backends, Ollama,
# RAM/disk, and how fast Admin Bulk edits really ran. Changes nothing.
#
#   ./scripts/gpu-report.sh > gpu-report.txt 2>&1
set -uo pipefail
APP=/data/apps/mixaicinemastudio
sec() { printf '\n===== %s\n' "$*"; }

sec "Cards"
nvidia-smi --query-gpu=index,name,memory.total,memory.used,power.limit,power.default_limit,power.max_limit,clocks.max.sm,temperature.gpu,pcie.link.gen.current,pcie.link.width.current,driver_version --format=csv
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv
nvidia-smi topo -m 2>/dev/null | head -8

sec "ComfyUI services and launch flags"
for u in $(systemctl list-units --all --type=service --plain --no-legend 'comfyui*' | awk '{print $1}'); do
  echo "-- $u"; systemctl show -p ExecStart --value "$u" | tr ';' '\n' | grep -E 'argv|path' | head -3
  systemctl show -p Environment --value "$u"
done
for p in 8188 8189; do
  echo "-- :$p system_stats"
  curl -s "http://127.0.0.1:$p/system_stats" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('system',{}); print('argv:', ' '.join(s.get('argv',[]))); print('comfy', s.get('comfyui_version'), 'python', s.get('python_version','')[:7], 'torch', s.get('pytorch_version')); [print('  dev', x.get('name'), 'vram_free', round(x.get('vram_free',0)/2**30,1), 'GB') for x in d.get('devices',[])]" 2>/dev/null
done

sec "Attention / speed libraries in ComfyUI's venv"
/data/venvs/comfy/bin/python - <<'EOF' 2>/dev/null
import importlib
for m in ("torch", "triton", "sageattention", "flash_attn", "xformers", "comfy_kitchen", "bitsandbytes", "transformers"):
    try:
        mod = importlib.import_module(m); print(f"{m:15} {getattr(mod, '__version__', 'present')}")
    except Exception as e:
        print(f"{m:15} missing")
import torch
print("cuda", torch.version.cuda, "| devices", [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])
EOF

sec "Ollama"
systemctl show -p Environment --value ollama 2>/dev/null | tr ' ' '\n' | grep -i -E 'OLLAMA|CUDA'
ollama ps 2>/dev/null

sec "Machine"
lscpu | grep -E 'Model name|^CPU\(s\)|Thread'
free -h
df -h /data | tail -1
lsblk -d -o NAME,ROTA,SIZE,MODEL | grep -v loop

sec "App settings that shape GPU use"
grep -E '^(COMFY_|BULK_|EDIT_SET|VIDEO_FRAMES|LLM_MODEL|AUDIO_GPU)' "$APP/.env" 2>/dev/null

sec "Admin Bulk: real speed per picture (last 7 days)"
cd "$APP/backend" && uv run python - <<'EOF' 2>/dev/null
from datetime import timedelta
from statistics import median
from sqlalchemy import select
from app.db import SessionLocal
from app.models import BulkBatch, BulkItem, Job, utcnow
with SessionLocal() as db:
    for b in db.scalars(select(BulkBatch).where(BulkBatch.created_at >= utcnow() - timedelta(days=7))):
        secs = []
        for it in db.scalars(select(BulkItem).where(BulkItem.batch_id == b.id, BulkItem.status == "done")):
            j = db.get(Job, it.job_id or "")
            if j and j.started_at and j.finished_at:
                secs.append((j.finished_at - j.started_at).total_seconds())
        model = (b.options or {}).get("model") or (b.options or {}).get("engine") or "-"
        print(f"{b.name[:30]:30} {b.action:7} model={model:24} done={len(secs):5}",
              f"median={median(secs):.1f}s p90={sorted(secs)[int(len(secs)*0.9)]:.1f}s" if secs else "")
EOF
