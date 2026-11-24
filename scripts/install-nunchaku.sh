#!/usr/bin/env bash
# Nunchaku (SVDQuant INT4) for ComfyUI: Qwen-Image-Edit 2511 in ~13 GB instead of ~20, so the bulk edit stays on
# the 20 GB card instead of spilling to system RAM on every picture (speed trial, 24 Nov).
#
#   ./scripts/install-nunchaku.sh             # build, install, download, restart ComfyUI, smoke-test
#   ./scripts/install-nunchaku.sh --no-build  # skip the compile (already built), redo the rest
#   ./scripts/install-nunchaku.sh --dry-run
#
# Why a compile: Nunchaku's official wheels stop at torch 2.11 and our ComfyUI runs torch 2.14, so the CUDA
# kernels are built here against the installed torch, for our cards only (sm_89). 30-90 min, CPU-bound, and it
# may fail on a torch it was never tested with: then nothing in ComfyUI has changed and the trial is off.
#
# Pieces (all Apache-2.0): nunchaku core (nunchaku-ai/nunchaku, pinned tag), the ComfyUI-nunchaku node pack (pinned
# tag), and QuantFunc's community INT4 re-quantisation of Qwen-Image-Edit-2511 ("balance", rank 128, 12.7 GB).
# Env: COMFY_ROOT, COMFY_VENV, NUNCHAKU_REF, NODES_REF, MAX_JOBS, HF_TOKEN.
set -euo pipefail

NUNCHAKU_URL=https://github.com/nunchaku-ai/nunchaku.git
NUNCHAKU_REF="${NUNCHAKU_REF:-v1.2.1}"
NODES_URL=https://github.com/nunchaku-ai/ComfyUI-nunchaku.git
NODES_REF="${NODES_REF:-v1.2.0}"
SRC=/data/apps/nunchaku-src
MODEL_REPO=QuantFunc/Nunchaku-Qwen-Image-EDIT-2511
MODEL_FILE=nunchaku_qwen_image_edit_2511_balance_int4.safetensors
DRY=0 BUILD=1

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-build) BUILD=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[0;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\033[0;31mxx %s\033[0m\n' "$*" >&2; exit 1; }
run()  { if (( DRY )); then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }
svc_exec() { systemctl show -p ExecStart --value "$1" 2>/dev/null || true; }

# same lookup as install-caption-nodes.sh
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

if "$PY" -m pip --version >/dev/null 2>&1; then pipx() { "$PY" -m pip "$@"; }
elif command -v uv >/dev/null; then pipx() { local c="$1"; shift; uv pip "$c" --python "$PY" "$@"; }
else die "no pip in $COMFY_VENV and no uv on PATH"; fi

say "ComfyUI: $COMFY_ROOT   venv: $COMFY_VENV"
TORCH=$("$PY" -c "import torch; print(torch.__version__, torch.version.cuda)")
info "torch / CUDA in the venv: $TORCH"

if (( BUILD )); then
  say "Build tools"
  command -v nvcc >/dev/null || for d in /usr/local/cuda/bin /usr/local/cuda-13*/bin; do [[ -x $d/nvcc ]] && export PATH="$d:$PATH" && break; done
  command -v nvcc >/dev/null || die "no nvcc (CUDA toolkit). Install the toolkit matching torch's CUDA (${TORCH#* }), e.g. sudo apt install cuda-toolkit-13-0"
  NVCC=$(nvcc --version | grep -oE 'release [0-9.]+' | cut -d' ' -f2)
  GCC=$(gcc -dumpversion 2>/dev/null | cut -d. -f1)
  info "nvcc $NVCC   gcc $GCC"
  [[ "${TORCH#* }" == "$NVCC"* || "$NVCC" == "${TORCH#* }"* ]] || warn "nvcc $NVCC differs from torch's CUDA ${TORCH#* }; the build may refuse"
  (( ${GCC:-0} >= 11 )) || die "gcc $GCC is too old; Nunchaku needs gcc 11 or newer"

  say "Nunchaku $NUNCHAKU_REF source"
  if [[ -d "$SRC/.git" ]]; then run git -C "$SRC" fetch --quiet --tags origin
  else run git clone --quiet "$NUNCHAKU_URL" "$SRC"; fi
  run git -C "$SRC" checkout --quiet "$NUNCHAKU_REF"
  run git -C "$SRC" submodule update --init --recursive --quiet

  say "Compiling for sm_89 against torch ${TORCH% *} (30-90 min; it prints little while nvcc works)"
  # each nvcc job wants several GB of RAM; half the cores keeps the 96 GB box (and ComfyUI) comfortable
  # no NUNCHAKU_INSTALL_MODE=ALL: the default builds for the cards it finds, far quicker than every arch
  export MAX_JOBS="${MAX_JOBS:-$(( $(nproc) / 2 ))}" TORCH_CUDA_ARCH_LIST="8.9"
  # constraints keep torch exactly where ComfyUI has it; --no-build-isolation builds against that same torch
  cons="$(mktemp)"; pipx freeze 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|numpy|transformers|diffusers)==' > "$cons" || true
  t0=$SECONDS
  if (( ! DRY )); then
    log="$SRC/build.log"
    info "full log: $log"
    if ! pipx install -v --no-build-isolation -c "$cons" "$SRC" > "$log" 2>&1; then
      # the traceback at the end is setuptools noise; the real cause is nvcc/gcc's own error lines
      grep -nE '(error|Error|FAILED|fatal)' "$log" | grep -v 'Traceback\|raise RuntimeError' | head -n 40
      echo "    ..."; tail -n 15 "$log"
      die "the build failed (errors above, full log in $log). ComfyUI is unchanged; send me these lines"
    fi
  fi
  rm -f "$cons"
  info "built in $(( (SECONDS - t0) / 60 )) min"
fi
(( DRY )) || "$PY" -c "import nunchaku, torch; print('    nunchaku', getattr(nunchaku, '__version__', '?'), '| torch', torch.__version__)" \
  || die "nunchaku doesn't import (see above)"

say "ComfyUI-nunchaku $NODES_REF"
dir="$COMFY_ROOT/custom_nodes/ComfyUI-nunchaku"
if [[ -d "$dir/.git" ]]; then run git -C "$dir" fetch --quiet --tags origin
else run git clone --quiet "$NODES_URL" "$dir"; fi
run git -C "$dir" checkout --quiet "$NODES_REF"
if [[ -f "$dir/requirements.txt" ]] && (( ! DRY )); then
  cons="$(mktemp)"; req="$(mktemp)"
  pipx freeze 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|numpy|transformers|diffusers|nunchaku)==' > "$cons" || true
  grep -viE '^(torch|torchvision|torchaudio|nunchaku)([^a-z0-9_-]|$)' "$dir/requirements.txt" | tr -d '\r' | sed -e 's/#.*//' | grep -v '^\s*$' > "$req" || true
  [[ -s "$req" ]] && { pipx install --quiet -c "$cons" -r "$req" || die "couldn't install the node pack's packages"; }
  rm -f "$cons" "$req"
fi

say "INT4 model ($MODEL_FILE, 12.7 GB)"
dest="$COMFY_ROOT/models/diffusion_models"
if [[ -s "$dest/$MODEL_FILE" ]]; then info "already there"
else
  run "$PY" -c "from huggingface_hub import hf_hub_download as d; d('$MODEL_REPO', '$MODEL_FILE', local_dir='$dest')"
fi
# the Lightning LoRA, text encoder and VAE are the ones qwen_edit already uses

(( DRY )) && { say "Dry run: nothing changed."; exit 0; }

mapfile -t UNITS < <(systemctl list-units --all --type=service --plain --no-legend 'comfyui*' 2>/dev/null | awk '{print $1}')
(( ${#UNITS[@]} )) || die "no comfyui*.service found - restart ComfyUI by hand, then run scripts/nunchaku-smoke.py"
say "Restarting: ${UNITS[*]}"
sudo systemctl restart "${UNITS[@]}"
for port in 8188 8189; do
  printf '    waiting for :%s ' "$port"
  for _ in $(seq 1 90); do curl -sf -o /dev/null "http://127.0.0.1:$port/system_stats" && break; printf '.'; sleep 2; done
  echo
done

say "Smoke test on :8188 (one edit, timed twice: cold then warm)"
python3 "$(dirname "$0")/nunchaku-smoke.py" --url http://127.0.0.1:8188 \
  || die "the INT4 edit didn't run (above). Production edits are untouched; send me this output"
say "Nunchaku ready. Next: the bulk benchmark (scripts/bench-bulk-edit.py --help)"
