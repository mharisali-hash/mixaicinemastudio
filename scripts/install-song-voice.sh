#!/usr/bin/env bash
# Songs sung by a child, a choir or a Library voice: sets up Seed-VC (singing voice conversion) in its own
# venv and downloads its singing model. Run as the user the workers run as (gpu). Safe to re-run.
#   ./scripts/install-song-voice.sh
# Then put SONG_VOICE_ENGINE=seed-vc in .env and restart the workers.
#
# Seed-VC is GPL-3.0 (code and weights). It runs as a separate program on our own server and is never
# shipped to anyone, which GPL allows; keep it that way (don't import it into the app).
set -euo pipefail

VENVS="${AUDIO_VENVS_DIR:-/data/venvs}"
DIR="$VENVS/seed-vc"
SRC="$DIR/src"
# pinned so a later upstream change can't alter the voices without us knowing; bump on purpose
REF="${SEED_VC_REF:-main}"

command -v uv >/dev/null || { echo "uv is missing (scripts/install-server.sh installs it)"; exit 1; }
command -v git >/dev/null || { echo "git is missing"; exit 1; }

if [[ ! -d $SRC/.git ]]; then
  echo "==> cloning Seed-VC"
  git clone https://github.com/Plachtaa/seed-vc.git "$SRC"
fi
git -C "$SRC" fetch --quiet origin
git -C "$SRC" checkout --quiet "$REF"
[[ $REF == main ]] && git -C "$SRC" pull --quiet --ff-only origin main
echo "    Seed-VC at $(git -C "$SRC" rev-parse --short HEAD)  (set SEED_VC_REF to pin it)"

if [[ ! -x $DIR/bin/python ]]; then
  echo "==> venv (Python 3.10, what Seed-VC is tested on)"
  # the checkout already lives in $DIR/src, so the folder exists; keep it
  uv venv --python 3.10 --allow-existing "$DIR"
fi
echo "==> PyTorch"
# Seed-VC's requirements.txt mixes nightly lines with torch 2.4, which doesn't know the newest cards.
# Its own venv gets one matched pair instead: 2.7.1 is the first with Blackwell (sm_120) kernels and still
# has a torchaudio to go with it, and CUDA 12.8 runs on any newer driver. TORCH_VERSION / TORCH_INDEX override.
TORCH_VERSION="${TORCH_VERSION:-2.7.1}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
echo "    torch $TORCH_VERSION + torchaudio $TORCH_VERSION from $TORCH_INDEX"
uv pip install --python "$DIR/bin/python" -q "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION" \
  --index-url "$TORCH_INDEX" --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match

echo "==> requirements"
# everything else it lists, minus its own torch lines and the desktop GUI pieces inference.py never imports
grep -vE '^(torch|torchvision|torchaudio)([ =<>]|$)|^(gradio|FreeSimpleGUI|sounddevice)([ =<>]|$)' \
  "$SRC/requirements.txt" > "$DIR/requirements.server.txt"
# nothing in that list may swap the pair above (plain versions: a +cu tag here can't be matched)
printf 'torch==%s\ntorchaudio==%s\n' "$TORCH_VERSION" "$TORCH_VERSION" > "$DIR/constraints.txt"
uv pip install --python "$DIR/bin/python" -q -r "$DIR/requirements.server.txt" -c "$DIR/constraints.txt" \
  --extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match
"$DIR/bin/python" -c 'import torch; print("    torch", torch.__version__, "cuda", torch.cuda.is_available())'

echo "==> singing model (one short conversion downloads it)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
ff=$(command -v ffmpeg || true)
if [[ -z $ff ]]; then echo "ffmpeg is missing"; exit 1; fi
"$ff" -loglevel error -f lavfi -i "sine=frequency=330:duration=3" -ar 44100 "$tmp/src.wav"
"$ff" -loglevel error -f lavfi -i "sine=frequency=220:duration=3" -ar 44100 "$tmp/ref.wav"
# the same model cache the workers use (AUDIO_HF_HOME in .env), so the first real song doesn't download again
env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
if [[ -z ${AUDIO_HF_HOME:-} && -f $env_file ]]; then
  AUDIO_HF_HOME=$(grep -E '^AUDIO_HF_HOME=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '"'"'" || true)
fi
if [[ -n ${AUDIO_HF_HOME:-} ]]; then export HF_HOME=$AUDIO_HF_HOME; fi
# on the GPU, the way the worker runs it: ComfyUI usually holds most of the card, so ask it to let go first
# (what venv_runner.ensure_vram does). The CPU path crashes in Seed-VC's vocoder, so it's no test.
comfy=""
if [[ -f $env_file ]]; then
  comfy=$(grep -E '^COMFY_(IMAGE_)?URLS=' "$env_file" | head -n1 | cut -d= -f2- | tr -d '"'"'" | cut -d, -f1 || true)
fi
if [[ -n $comfy ]]; then
  curl -s -m 20 -X POST "$comfy/free" -H 'Content-Type: application/json' \
    -d '{"unload_models": true, "free_memory": true}' >/dev/null && echo "    asked ComfyUI ($comfy) to free the GPU"
  sleep 3
fi
set +e
(cd "$SRC" && CUDA_VISIBLE_DEVICES="${GPU:-0}" "$DIR/bin/python" inference.py --source "$tmp/src.wav" \
  --target "$tmp/ref.wav" --output "$tmp/out" --diffusion-steps 4 --f0-condition True --auto-f0-adjust False \
  --semi-tone-shift 0 --fp16 True) > "$tmp/log" 2>&1
rc=$?
set -e
# some builds crash on the way out after the file is written; the file is what counts
if ls "$tmp/out"/*.wav >/dev/null 2>&1; then
  if (( rc )); then echo "    OK: Seed-VC converts (it exited with $rc after writing)"; else echo "    OK: Seed-VC converts"; fi
else
  tail -n 15 "$tmp/log"
  echo "!! no output from Seed-VC (exit $rc)"
  exit 1
fi

echo
echo "Done. Add SONG_VOICE_ENGINE=seed-vc to .env, then: sudo systemctl restart 'mixai-worker@*'"
