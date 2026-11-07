#!/usr/bin/env bash
# Mix AI Cinema Studio - Linux server install / update.
#
#   ./scripts/install-server.sh --admin-email you@example.com --public-url https://your.host:8443 --systemd
#
# Safe to re-run: an existing .env is never overwritten (the only exception: with GPU lanes on, missing
# COMFY_IMAGE_URLS / COMFY_VIDEO_URLS are filled in, after a backup), the DB is migrated in place,
# and services are restarted at the end.
#   --gpu-lanes / --no-gpu-lanes  one worker per GPU (image, video) plus one for LLM/ffmpeg work. Both GPU
#                                 workers also take audio jobs, whichever is free first.
#                                 Default: on when both lane URLs are in .env or two ComfyUIs are found.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
DATA_DIR="${DATA_DIR:-/data/outputs/mixai}"
COMFY_ROOT="${COMFY_ROOT:-/data/apps/ComfyUI}"
API_PORT="${API_PORT:-8090}"
ADMIN_EMAIL=""
PUBLIC_URL=""
INSTALL_SYSTEMD=0
GPU_LANES=auto
# both GPU workers serve the audio lane (mixai-worker@image-audio, @video-audio)
LANES=(image-audio video-audio general)
# instance names from before the audio lane; left running they'd serve image / video twice
OLD_LANES=(image video)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
    --public-url)  PUBLIC_URL="$2"; shift 2 ;;
    --data-dir)    DATA_DIR="$2"; shift 2 ;;
    --comfy-root)  COMFY_ROOT="$2"; shift 2 ;;
    --port)        API_PORT="$2"; shift 2 ;;
    --user)        SERVICE_USER="$2"; shift 2 ;;
    --systemd)     INSTALL_SYSTEMD=1; shift ;;
    --gpu-lanes)   GPU_LANES=1; shift ;;
    --no-gpu-lanes) GPU_LANES=0; shift ;;
    -h|--help)     sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[0;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\033[0;31mxx %s\033[0m\n' "$*" >&2; exit 1; }

# --- prerequisites -----------------------------------------------------------
say "Checking prerequisites"
if ! command -v uv >/dev/null 2>&1; then
  echo "    installing uv (user-local)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null || die "uv not on PATH; open a new shell or add ~/.local/bin to PATH"
command -v node >/dev/null || die "Node 20+ is needed to build the frontend: https://nodejs.org (or nodesource)"
node_major=$(node -p 'process.versions.node.split(".")[0]')
(( node_major >= 20 )) || die "Node $node_major found; need 20+"
command -v ffmpeg >/dev/null || warn "ffmpeg not on PATH; the app will fall back to its bundled copy"
echo "    uv $(uv --version | cut -d' ' -f2), node $(node -v)"

# --- subtitle fonts --------------------------------------------------------------
# burned subtitles want Noto per script: Nastaliq (Urdu) ships in fonts-noto-extra, Naskh / Sans / Devanagari
# in fonts-noto-core. libfribidi lets Pillow's raqm layout shape the fallback renderer.
say "Checking subtitle fonts"
SUB_PKGS=(fonts-noto-core fonts-noto-extra libfribidi0)
if command -v dpkg-query >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  missing_pkgs=()
  for pkg in "${SUB_PKGS[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || missing_pkgs+=("$pkg")
  done
  if (( ${#missing_pkgs[@]} )); then
    echo "    installing ${missing_pkgs[*]} (sudo)…"
    if sudo apt-get install -y --no-install-recommends "${missing_pkgs[@]}" >/dev/null; then
      command -v fc-cache >/dev/null 2>&1 && sudo fc-cache -f >/dev/null 2>&1 || true
    else
      warn "couldn't install ${missing_pkgs[*]}; subtitles fall back to whatever fonts are there"
    fi
  fi
else
  warn "no apt here; install the Noto fonts (Nastaliq Urdu, Naskh Arabic, Sans, Sans Devanagari) yourself"
fi
if command -v fc-list >/dev/null 2>&1; then
  for fam in "Noto Nastaliq Urdu" "Noto Naskh Arabic" "Noto Sans" "Noto Sans Devanagari"; do
    if [[ -n "$(fc-list "$fam" file 2>/dev/null | head -n1)" ]]; then
      echo "    font ok: $fam"
    else
      warn "font missing: $fam"
    fi
  done
fi
if command -v ffmpeg >/dev/null 2>&1 && ! ffmpeg -hide_banner -h filter=ass 2>/dev/null | grep -q shaping; then
  warn "this ffmpeg's ass filter can't shape (no libass/HarfBuzz); Urdu/Arabic subtitles will be drawn with Pillow"
fi

# --- backend + frontend --------------------------------------------------------
say "Installing backend (Python 3.12 venv via uv)"
(cd "$APP_DIR/backend" && uv sync --frozen)

say "Building frontend"
(cd "$APP_DIR/frontend" && npm ci --no-audit --no-fund && npm run build)

# --- animated captions and titles (app.overlays) --------------------------------
# HyperFrames (Apache-2.0) draws them in a headless Chrome. It needs Node 22+; without it the app keeps the
# plain burned subtitles. Its telemetry and update checks are switched off here and in the worker.
say "Animated captions (HyperFrames)"
export HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1 HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_AUTO_INSTALL=1 \
  HYPERFRAMES_SKIP_SKILLS=1 HYPERFRAMES_NO_OPTIONAL_INSTALL=1
if (( node_major >= 22 )); then
  if (cd "$APP_DIR/tools/hyperframes" && npm ci --no-audit --no-fund >/dev/null \
      && node node_modules/hyperframes/bin/hyperframes.mjs browser ensure >/dev/null); then
    echo "    renderer ok"
    if [[ "$(uname -s)" == Linux ]]; then
      (cd "$APP_DIR/tools/hyperframes" && node node_modules/hyperframes/bin/hyperframes.mjs doctor 2>&1 | tail -n 12) || true
    fi
  else
    warn "couldn't set up HyperFrames; videos keep the classic burned subtitles"
  fi
else
  warn "Node $node_major found; animated captions need Node 22+ (videos keep the classic subtitles)"
fi

# --- .env ----------------------------------------------------------------------
ENV_FILE="$APP_DIR/.env"
set_kv() {  # replace the active KEY= line, or append it
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

env_get() {  # active value of KEY in .env, inline comment stripped
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^${1}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | sed -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//' || true
}
port_listening() {
  command -v ss >/dev/null 2>&1 || return 1
  ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}\$"
}
comfy_services() {  # comfyui.service, comfyui-gpu1.service, ...
  command -v systemctl >/dev/null 2>&1 || { echo 0; return; }
  systemctl list-unit-files --type=service --no-legend 'comfyui*' 2>/dev/null | wc -l
}
# image on GPU 0 (8188), video on GPU 1 (8189): swapping the GPUs is swapping these two values in .env
IMAGE_URL=http://127.0.0.1:8188
VIDEO_URL=http://127.0.0.1:8189
TWO_COMFY=0
if port_listening 8188 && port_listening 8189; then TWO_COMFY=1; fi

GENERATED_PW=""
if [[ ! -f "$ENV_FILE" ]]; then
  say "Creating .env (server profile)"
  [[ -n "$ADMIN_EMAIL" ]] || read -rp "    Admin email: " ADMIN_EMAIL
  [[ -n "$PUBLIC_URL" ]]  || read -rp "    Public URL (e.g. https://your.host:8443): " PUBLIC_URL
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  GENERATED_PW="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
  set_kv APP_ENV prod
  set_kv APP_SECRET "$(openssl rand -hex 32)"
  set_kv APP_HOST 127.0.0.1
  set_kv APP_PORT "$API_PORT"
  set_kv CORS_ORIGINS "$PUBLIC_URL"
  set_kv ADMIN_EMAIL "$ADMIN_EMAIL"
  set_kv ADMIN_PASSWORD "$GENERATED_PW"
  set_kv DEV_LOGIN_PREFILL false
  set_kv DATA_DIR "$DATA_DIR"
  set_kv DATABASE_URL "sqlite:///${DATA_DIR}/mixai.db"
  set_kv GEN_DRIVER comfy
  if (( TWO_COMFY )); then
    echo "    ComfyUI answers on 8188 and 8189: images -> GPU 0 (8188), video -> GPU 1 (8189)"
    set_kv COMFY_URLS "${IMAGE_URL},${VIDEO_URL}"
    set_kv COMFY_IMAGE_URLS "$IMAGE_URL"
    set_kv COMFY_VIDEO_URLS "$VIDEO_URL"
  else
    set_kv COMFY_URLS http://127.0.0.1:8188
  fi
  set_kv COMFY_VERIFY_TLS true
  set_kv COMFY_OUTPUT_DIR "${COMFY_ROOT}/output"
  set_kv COMFY_LORA_DIR "${COMFY_ROOT}/models/loras"
  set_kv LLM_BASE_URL http://127.0.0.1:11434/v1
  set_kv LLM_MODEL_REASONING deepseek-r1:14b
  set_kv LLM_MODEL_CREATIVE gemma4:e4b
  set_kv LLM_MODEL_VISION gemma4:e4b
else
  say ".env exists - leaving it untouched"
fi

# --- GPU lanes -----------------------------------------------------------------------
if [[ "$GPU_LANES" == auto ]]; then
  GPU_LANES=0
  if [[ -n "$(env_get COMFY_IMAGE_URLS)" && -n "$(env_get COMFY_VIDEO_URLS)" ]]; then
    GPU_LANES=1; why="COMFY_IMAGE_URLS and COMFY_VIDEO_URLS are set"
  elif (( $(comfy_services) >= 2 )) || (( TWO_COMFY )); then
    GPU_LANES=1; why="two ComfyUI instances found"
  fi
  if (( GPU_LANES )); then echo "    GPU lanes: on ($why)"; fi
fi
if (( GPU_LANES )) && [[ -z "$(env_get COMFY_IMAGE_URLS)" || -z "$(env_get COMFY_VIDEO_URLS)" ]]; then
  if (( TWO_COMFY )); then
    backup="$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$ENV_FILE" "$backup"
    set_kv COMFY_IMAGE_URLS "$IMAGE_URL"
    set_kv COMFY_VIDEO_URLS "$VIDEO_URL"
    echo "    .env: set COMFY_IMAGE_URLS=$IMAGE_URL COMFY_VIDEO_URLS=$VIDEO_URL (backup: $backup)"
  else
    warn "GPU lanes on, but COMFY_IMAGE_URLS / COMFY_VIDEO_URLS aren't both set and 8188+8189 aren't both"
    warn "listening; every lane falls back to COMFY_URLS. Set them in .env and re-run."
  fi
fi
# the voice engines' models sit in the HF cache the installing shell uses; the workers must look there too
if [[ -z "$(env_get AUDIO_HF_HOME)" && -n "${HF_HOME:-}" && -d "$HF_HOME" ]]; then
  set_kv AUDIO_HF_HOME "$HF_HOME"
  echo "    .env: set AUDIO_HF_HOME=$HF_HOME"
fi

# --- data + database -------------------------------------------------------------
say "Preparing data dir and database"
mkdir -p "$DATA_DIR"
(cd "$APP_DIR/backend" && uv run alembic upgrade head)
# photos uploaded before the catalogue get their EXIF and duplicate hash; quick when nothing is left
(cd "$APP_DIR/backend" && uv run python -m app.manage catalogue-backfill) || warn "catalogue backfill failed (photos still work)"

# --- systemd -------------------------------------------------------------------------
if (( INSTALL_SYSTEMD )); then
  say "Installing systemd services (sudo)"
  for unit in mixai-api mixai-worker; do
    sed -e "s|/data/apps/mixaicinemastudio|${APP_DIR}|g" \
        -e "s|^User=.*|User=${SERVICE_USER}|" \
        -e "s|--port 8090|--port ${API_PORT}|" \
        "$APP_DIR/deploy/${unit}.service" | sudo tee "/etc/systemd/system/${unit}.service" >/dev/null
  done
  sed -e "s|/data/apps/mixaicinemastudio|${APP_DIR}|g" \
      -e "s|^User=.*|User=${SERVICE_USER}|" \
      "$APP_DIR/deploy/mixai-worker@.service" | sudo tee /etc/systemd/system/mixai-worker@.service >/dev/null
  sudo systemctl daemon-reload
  lane_units=("${LANES[@]/#/mixai-worker@}")
  lane_units=("${lane_units[@]/%/.service}")
  old_units=("${OLD_LANES[@]/#/mixai-worker@}")
  old_units=("${old_units[@]/%/.service}")
  for u in "${old_units[@]}"; do
    if systemctl is-enabled --quiet "$u" 2>/dev/null || systemctl is-active --quiet "$u" 2>/dev/null; then
      sudo systemctl disable --now "$u" >/dev/null 2>&1 || true
      echo "    stopped and disabled $u (replaced by ${u%.service}-audio.service)"
    fi
  done
  if (( GPU_LANES )); then
    # the single all-lanes worker would race the lane workers for jobs; it goes first
    if systemctl is-enabled --quiet mixai-worker 2>/dev/null || systemctl is-active --quiet mixai-worker 2>/dev/null; then
      sudo systemctl disable --now mixai-worker >/dev/null 2>&1 || true
      echo "    stopped and disabled mixai-worker.service (replaced by lane workers)"
    fi
    sudo systemctl enable mixai-api "${lane_units[@]}" >/dev/null
    sudo systemctl restart mixai-api "${lane_units[@]}"
    echo "    enabled + restarted ${lane_units[*]}"
    workers=("${lane_units[@]}")
  else
    for u in "${lane_units[@]}"; do
      if systemctl is-enabled --quiet "$u" 2>/dev/null || systemctl is-active --quiet "$u" 2>/dev/null; then
        sudo systemctl disable --now "$u" >/dev/null 2>&1 || true
        echo "    stopped and disabled $u (single worker mode)"
      fi
    done
    sudo systemctl enable mixai-api mixai-worker >/dev/null
    sudo systemctl restart mixai-api mixai-worker
    workers=(mixai-worker)
  fi
  sleep 3
  systemctl --no-pager --lines=0 status mixai-api "${workers[@]}" || true
fi

# --- smoke test ----------------------------------------------------------------------
if (( INSTALL_SYSTEMD )); then
  say "Smoke test"
  if curl -fsS "http://127.0.0.1:${API_PORT}/api/health" >/dev/null; then
    echo "    API healthy on 127.0.0.1:${API_PORT}"
  else
    warn "API not answering yet - check: journalctl -u mixai-api -n 50"
  fi
fi

say "Done"
if [[ -n "$GENERATED_PW" ]]; then
  echo "    Admin login: ${ADMIN_EMAIL}"
  echo "    Admin password (shown once, change it in the app or with app.manage): ${GENERATED_PW}"
fi
cat <<EOF
    Next:
      - Caddy: add deploy/Caddyfile.mixai (replace SERVER_IP), then: sudo systemctl reload caddy
      - Open ${PUBLIC_URL:-your public URL} and sign in
      - Checks: curl -s http://127.0.0.1:${API_PORT}/api/health
EOF
if (( ! INSTALL_SYSTEMD )); then
  echo "      - Run without systemd: cd backend && uv run uvicorn app.main:app --port ${API_PORT}"
  if (( GPU_LANES )); then
    echo "        and one worker per lane: uv run python -m app.worker --lanes image,audio  (then video,audio and general)"
  else
    echo "        and: uv run python -m app.worker"
  fi
fi
