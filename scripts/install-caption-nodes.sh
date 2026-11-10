#!/usr/bin/env bash
# Mix AI Cinema Studio - JoyCaption captioning node for ComfyUI (both GPUs read the same ComfyUI folder).
#
#   ./scripts/install-caption-nodes.sh             # install the node, restart ComfyUI, check it loaded
#   ./scripts/install-caption-nodes.sh --download  # also fetch JoyCaption Beta One now (~16 GB) instead of on first use
#   ./scripts/install-caption-nodes.sh --dry-run
#
# Node: 1038lab/ComfyUI-JoyCaption, GPL-3.0. It runs inside ComfyUI on our server and is never shipped with the
# app, the same rule as Seed-VC. Model: fancyfeast/llama-joycaption-beta-one-hf-llava (Llama 3.1 8B + SigLIP):
# check its licence before captions go to customers (Llama 3.1 community licence terms apply to the base).
# The node keeps its models in ComfyUI/models/LLM/<name>. Env: COMFY_ROOT, COMFY_VENV, HF_TOKEN.
set -euo pipefail

NODE_DIR=ComfyUI-JoyCaption
NODE_URL=https://github.com/1038lab/ComfyUI-JoyCaption.git
NODE_PIN=a0e9f0a17a5deb933fef341e2c7b0131e4f83c8a   # 2026-11-10
MODEL_REPO=fancyfeast/llama-joycaption-beta-one-hf-llava
DRY=0 DOWNLOAD=0

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --download) DOWNLOAD=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[0;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\033[0;31mxx %s\033[0m\n' "$*" >&2; exit 1; }
run()  { if (( DRY )); then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }
svc_exec() { systemctl show -p ExecStart --value "$1" 2>/dev/null || true; }

# same lookup as install-image-tools.sh
if [[ -z "${COMFY_ROOT:-}" ]]; then
  if [[ -d /data/apps/ComfyUI/models ]]; then COMFY_ROOT=/data/apps/ComfyUI
  else COMFY_ROOT="$(systemctl show -p WorkingDirectory --value comfyui.service 2>/dev/null || true)"; fi
fi
[[ -n "$COMFY_ROOT" && -d "$COMFY_ROOT/models" ]] || die "ComfyUI not found; set COMFY_ROOT=/path/to/ComfyUI"
if [[ -z "${COMFY_VENV:-}" ]]; then
  if [[ -x /data/venvs/comfy/bin/python ]]; then COMFY_VENV=/data/venvs/comfy
  else
    py="$(svc_exec comfyui.service | grep -oE 'path=[^ ;]+' | head -1 | cut -d= -f2)"
    [[ -n "$py" ]] && COMFY_VENV="$(dirname "$(dirname "$py")")"
  fi
fi
PY="${COMFY_VENV:-}/bin/python"
[[ -x "$PY" ]] || die "ComfyUI venv python not found; set COMFY_VENV=/path/to/venv"
[[ $EUID -ne 0 || "${ALLOW_ROOT:-0}" == 1 ]] || die "run as the user that owns $COMFY_ROOT (e.g. gpu), not root"

say "ComfyUI: $COMFY_ROOT   venv: $COMFY_VENV"
dir="$COMFY_ROOT/custom_nodes/$NODE_DIR"
if [[ -d "$dir/.git" ]]; then
  run git -C "$dir" fetch --quiet origin
  run git -C "$dir" checkout --quiet "$NODE_PIN"
  info "$NODE_DIR at ${NODE_PIN:0:10}"
else
  run git clone --quiet "$NODE_URL" "$dir"
  run git -C "$dir" checkout --quiet "$NODE_PIN"
  info "cloned $NODE_DIR at ${NODE_PIN:0:10}"
fi

say "Python packages"
# torch and friends belong to ComfyUI; pin what is installed so the node's requirements can't move them
cons="$(mktemp)"; req="$(mktemp)"
"$PY" -m pip freeze 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|numpy|xformers|triton|transformers)==' > "$cons" || true
if (( DRY )); then
  info "would install from $NODE_DIR/requirements.txt (torch excluded), keeping: $(tr '\n' ' ' < "$cons")"
else
  grep -viE '^(torch|torchvision|torchaudio)([^a-z0-9_-]|$)' "$dir/requirements.txt" | sed -e 's/#.*//' | grep -v '^\s*$' > "$req" || true
  "$PY" -m pip install --quiet -c "$cons" -r "$req" || {
    # an older transformers than the node asks for: let pip lift just that one
    warn "pip refused with transformers pinned; retrying with transformers free to move"
    grep -viE '^transformers==' "$cons" > "$cons.2" && "$PY" -m pip install --quiet -c "$cons.2" -r "$req"
  }
fi
rm -f "$cons" "$cons.2" "$req"

if (( DOWNLOAD )); then
  say "Downloading $MODEL_REPO into models/LLM (about 16 GB)"
  run "$PY" -c "from huggingface_hub import snapshot_download as d; d('$MODEL_REPO', local_dir='$COMFY_ROOT/models/LLM/${MODEL_REPO#*/}')"
else
  info "The model downloads on first use (about 16 GB, minutes). --download fetches it now."
fi

(( DRY )) && { say "Dry run: nothing changed."; exit 0; }

mapfile -t UNITS < <(systemctl list-units --all --type=service --plain --no-legend 'comfyui*' 2>/dev/null | awk '{print $1}')
(( ${#UNITS[@]} )) || { warn "no comfyui*.service found - restart ComfyUI by hand"; exit 0; }
say "Restarting: ${UNITS[*]}"
sudo systemctl restart "${UNITS[@]}"

for port in 8188 8189; do
  base="http://127.0.0.1:$port"
  printf '    waiting for %s ' "$base"
  for _ in $(seq 1 90); do curl -sf -o /dev/null "$base/system_stats" && break; printf '.'; sleep 2; done
  echo
  found=$(curl -s "$base/object_info" | "$PY" -c "import sys,json; print(' '.join(k for k in json.load(sys.stdin) if 'joycaption' in k.lower()))" 2>/dev/null || true)
  if [[ -n "$found" ]]; then info ":$port loaded: $found"
  else warn ":$port has no JoyCaption node - see: journalctl -u comfyui* -n 100 | grep -i joy"; fi
done
