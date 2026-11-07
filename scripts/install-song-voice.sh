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
echo "==> PyTorch (the same build as the other audio engines, so it fits this GPU)"
# Seed-VC's requirements.txt mixes nightly and torch 2.4 lines; 2.4 doesn't know the newest cards, so we use
# whatever already runs in audio-utils (or TORCH_SPEC / TORCH_INDEX to choose by hand)
ref_py="$VENVS/audio-utils/bin/python"
if [[ -z ${TORCH_SPEC:-} && -x $ref_py ]]; then
  ver=$("$ref_py" -c 'import torch; print(torch.__version__)' 2>/dev/null || true)
  ta=$("$ref_py" -c 'import torchaudio; print(torchaudio.__version__)' 2>/dev/null || true)
  if [[ $ver == *+cu* ]]; then
    TORCH_SPEC="torch==${ver%%+*}"
    TORCH_INDEX="https://download.pytorch.org/whl/${ver##*+}"
    [[ -n $ta ]] && AUDIO_SPEC="torchaudio==${ta%%+*}"
  fi
fi
TORCH_SPEC="${TORCH_SPEC:-torch}"
AUDIO_SPEC="${AUDIO_SPEC:-torchaudio}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
echo "    $TORCH_SPEC $AUDIO_SPEC from $TORCH_INDEX"
uv pip install --python "$DIR/bin/python" -q "$TORCH_SPEC" "$AUDIO_SPEC" --index-url "$TORCH_INDEX" \
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match

echo "==> requirements"
# everything else it lists, minus its own torch lines and the desktop GUI pieces inference.py never imports
grep -vE '^(torch|torchvision|torchaudio)([ =<>]|$)|^(gradio|FreeSimpleGUI|sounddevice)([ =<>]|$)' \
  "$SRC/requirements.txt" > "$DIR/requirements.server.txt"
# and nothing in that list may swap the torch we just chose
"$DIR/bin/python" -c 'import torch, torchaudio; print(f"torch=={torch.__version__}\ntorchaudio=={torchaudio.__version__}")' \
  > "$DIR/constraints.txt"
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
if [[ -n ${AUDIO_HF_HOME:-} ]]; then export HF_HOME=$AUDIO_HF_HOME; fi
(cd "$SRC" && "$DIR/bin/python" inference.py --source "$tmp/src.wav" --target "$tmp/ref.wav" --output "$tmp/out" \
  --diffusion-steps 4 --f0-condition True --auto-f0-adjust False --semi-tone-shift 0 --fp16 True) | tail -n 3
ls "$tmp/out"/*.wav >/dev/null 2>&1 && echo "    OK: Seed-VC converts" || { echo "!! no output from Seed-VC"; exit 1; }

echo
echo "Done. Add SONG_VOICE_ENGINE=seed-vc to .env, then: sudo systemctl restart 'mixai-worker@*'"
