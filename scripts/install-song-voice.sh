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
echo "==> requirements"
uv pip install --python "$DIR/bin/python" -q -r "$SRC/requirements.txt"
# the runner protocol (backend/runners/runner_io.py) only needs numpy, which the requirements bring

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
