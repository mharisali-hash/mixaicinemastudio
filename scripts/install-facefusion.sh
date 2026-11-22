#!/usr/bin/env bash
# Face swap, stage 1 (a private test on our server, no app UI yet): sets up FaceFusion in its own venv, fetches
# the models the swap uses and swaps one test frame on the video card. Run as the user the workers run as (gpu).
# Safe to re-run.
#   ./scripts/install-facefusion.sh
# Then, from backend/: uv run python ../scripts/facefusion-faces.py <video> --at 2,6,10
#                      uv run python ../scripts/facefusion-swap.py --video ... (see its --help)
#
# Licences: FaceFusion's code is OpenRAIL-AS. The models it downloads are not all free to use: InSwapper and the
# RetinaFace / SCRFD / ArcFace detectors are non-commercial, HyperSwap is research-only, GFPGAN is Apache-2.0,
# Live Portrait (the expression restorer) MIT. So it runs on our own server only, never shipped, and only in
# test mode (FACESWAP_ALLOW_NONCOMMERCIAL, on now). Keep it a separate program, like Seed-VC.
set -euo pipefail

VENVS="${AUDIO_VENVS_DIR:-/data/venvs}"
VENV="$VENVS/facefusion"
SRC="${FACEFUSION_REPO:-/data/apps/facefusion}"
# a real tag, so an upstream change can't alter the swaps without us knowing; bump on purpose
REF="${FACEFUSION_REF:-3.9.2}"
# FaceFusion's own onnxruntime choice (its installer.py): cuda@12, or cuda@13 on a driver that has CUDA 13
ORT_SET="${FACEFUSION_ONNXRUNTIME:-cuda@12}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v uv >/dev/null || { echo "uv is missing (scripts/install-server.sh installs it)"; exit 1; }
command -v git >/dev/null || { echo "git is missing"; exit 1; }
# FaceFusion refuses to start without all three
for tool in curl ffmpeg ffprobe; do
  command -v $tool >/dev/null || { echo "$tool is missing"; exit 1; }
done

if [[ ! -d $SRC/.git ]]; then
  echo "==> cloning FaceFusion"
  mkdir -p "$(dirname "$SRC")"
  git clone https://github.com/facefusion/facefusion.git "$SRC"
fi
git -C "$SRC" fetch --quiet --tags origin
git -C "$SRC" checkout --quiet "$REF"
echo "    FaceFusion $REF at $(git -C "$SRC" rev-parse --short HEAD)"

if [[ ! -x $VENV/bin/python ]]; then
  echo "==> venv (Python 3.12)"
  uv venv --python 3.12 "$VENV"
fi
PY="$VENV/bin/python"

echo "==> requirements"
# not its install.py: that wants conda (or --skip-conda) and runs whichever `pip` is first on PATH, which in a
# uv venv is the system's. The same thing by hand: its requirements minus the web UI (gradio) and onnxruntime,
# then the onnxruntime-gpu version its installer pins for $ORT_SET
grep -vE '^(gradio|gradio-rangeslider|onnxruntime)([ =<>]|$)' "$SRC/requirements.txt" > "$VENV/requirements.server.txt"
ort=$(cd "$SRC" && "$PY" -c 'import sys; from facefusion.installer import ONNXRUNTIME_SET as s; print("==".join(s[sys.argv[1]]))' "$ORT_SET")
[[ $ort == onnxruntime-gpu==* ]] || { echo "$ORT_SET isn't a CUDA choice in FaceFusion's installer ($ort)"; exit 1; }
echo "    ${ort/==/[cuda,cudnn]==}"
# the [cuda,cudnn] extras bring CUDA and cuDNN as pip wheels, so nothing depends on the box's own CUDA
uv pip install --python "$PY" -q -r "$VENV/requirements.server.txt" "${ort/==/[cuda,cudnn]==}"
# the wheels' libraries aren't on the loader path by themselves (backend/runners/facefusion_swap.py does the same)
nv_libs=$("$PY" -c 'import glob, os, sysconfig; print(":".join(sorted(glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "nvidia", "*", "lib")))))')
export LD_LIBRARY_PATH="$nv_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$PY" -c 'import onnxruntime as o; p = o.get_available_providers(); print("    onnxruntime", o.__version__, p); assert "CUDAExecutionProvider" in p, "no CUDA provider"'

if [[ ${FACEFUSION_DOWNLOAD_ALL:-0} == 1 ]]; then
  # every model of every processor, tens of GB; by default the test below fetches only what the swap uses
  echo "==> all models (force-download)"
  (cd "$SRC" && "$PY" facefusion.py force-download --download-scope lite)
fi

echo "==> test swap on the video card"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# FaceFusion's own test pictures (its tests use the same two)
assets=https://github.com/facefusion/facefusion-assets/releases/download/examples-3.0.0
curl -fsSL -o "$tmp/source.jpg" "$assets/source.jpg"
curl -fsSL -o "$tmp/target.mp4" "$assets/target-240p.mp4"
ffmpeg -loglevel error -i "$tmp/target.mp4" -frames:v 1 "$tmp/target.jpg"

env_file="$ROOT/.env"
envval() { grep -E "^$1=" "$env_file" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'" || true; }
gpu="${GPU:-$(envval AUDIO_GPU_VIDEO)}"
gpu="${gpu:-1}"
# ComfyUI on the video card keeps its models resident; ask it to let go first, as venv_runner.ensure_vram does
comfy=$(envval COMFY_VIDEO_URLS | cut -d, -f1)
[[ -z $comfy ]] && comfy=$(envval COMFY_URLS | cut -d, -f1)
if [[ -n $comfy ]]; then
  curl -s -m 20 -X POST "$comfy/free" -H 'Content-Type: application/json' \
    -d '{"unload_models": true, "free_memory": true}' >/dev/null && echo "    asked ComfyUI ($comfy) to free the GPU"
  sleep 3
fi

set +e
# the processors and models facefusion-swap.py uses, so their weights are on disk before the first real run
(cd "$SRC" && CUDA_VISIBLE_DEVICES="$gpu" "$PY" facefusion.py headless-run --jobs-path "$tmp/jobs" \
  --temp-path "$tmp" -s "$tmp/source.jpg" -t "$tmp/target.jpg" -o "$tmp/out.jpg" \
  --processors face_swapper expression_restorer face_enhancer --face-swapper-model inswapper_128_fp16 \
  --face-enhancer-model gfpgan_1.4 --expression-restorer-model live_portrait --execution-providers cuda) \
  > "$tmp/log" 2>&1
rc=$?
set -e
if [[ ! -s $tmp/out.jpg ]]; then
  tail -n 20 "$tmp/log"
  echo "!! FaceFusion made no picture (exit $rc)"
  exit 1
fi
echo "    OK: swapped a face ($(stat -c %s "$tmp/out.jpg") bytes)"

# and the face finder facefusion-faces.py uses, through the same runner protocol the app speaks
found=$(cd "$ROOT/backend/runners" && CUDA_VISIBLE_DEVICES="$gpu" "$PY" facefusion_faces.py 2>"$tmp/faces.log" \
  <<<"{\"repo\": \"$SRC\", \"images\": [\"$tmp/target.jpg\"]}" | grep '"done"' || true)
if [[ $found != *'"box"'* ]]; then
  tail -n 20 "$tmp/faces.log"
  echo "!! the face finder found nothing"
  exit 1
fi
echo "    OK: the face finder sees the test face"

echo
echo "FaceFusion ready. On the wedding video, from backend/:"
echo "  uv run python ../scripts/facefusion-faces.py <video or media id> --at 2,6,10 --photo <couple photo id>"
echo "  uv run python ../scripts/facefusion-swap.py --video <video> --photo <couple photo id> \\"
echo "      --ref-frame <groom frame> --ref-face <n> --ref-frame <bride frame> --ref-face <n> --workspace <id>"
