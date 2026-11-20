#!/usr/bin/env bash
# First tuning pass from docs/research/gpu-performance-guide-2026-11-20.md (steps B1, B2, B4):
#   B1  Ollama frees the GPU after 10 min (was 1 h) and keeps one model loaded
#   B2  ComfyUI on both cards: --fast fp8_matrix_mult cublas_ops, --preview-method none, --reserve-vram 0.6
#   B4  GPU persistence mode, vm.swappiness=10
#
#   ./scripts/tune-gpu.sh            # apply, restart ComfyUI + Ollama (running GPU jobs are interrupted and resume)
#   ./scripts/tune-gpu.sh --dry-run  # show what it would write
#   ./scripts/tune-gpu.sh --undo     # remove everything this script added and restart
#
# Everything goes into drop-in files named 90-mixai-tune.conf, so --undo is exact and the original units are untouched.
set -euo pipefail
DRY=0 UNDO=0
for a in "$@"; do
  case "$a" in --dry-run) DRY=1 ;; --undo) UNDO=1 ;; -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;; esac
done

say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
FLAGS="--fast fp8_matrix_mult cublas_ops --preview-method none --reserve-vram 0.6"
DROPIN=90-mixai-tune.conf
mapfile -t UNITS < <(systemctl list-units --all --type=service --plain --no-legend 'comfyui*' | awk '{print $1}')

write() {  # $1 path, stdin = content
  local body; body="$(cat)"
  if (( DRY )); then printf '    [dry-run] %s:\n%s\n' "$1" "$(sed 's/^/      /' <<<"$body")"; return; fi
  sudo mkdir -p "$(dirname "$1")"
  printf '%s\n' "$body" | sudo tee "$1" >/dev/null
  info "wrote $1"
}

if (( UNDO )); then
  say "Removing the tuning"
  for u in "${UNITS[@]}" ollama.service; do sudo rm -f "/etc/systemd/system/$u.d/$DROPIN"; done
  sudo rm -f /etc/sysctl.d/90-mixai-gpu.conf
  sudo systemctl daemon-reload
  sudo systemctl restart "${UNITS[@]}" ollama
  info "back to the original settings (persistence mode stays on until the next reboot)"
  exit 0
fi

say "B2: ComfyUI flags"
for u in "${UNITS[@]}"; do
  # rebuild the unit's own command line (minus anything we added before) and append the flags
  argv=$(systemctl show -p ExecStart --value "$u" | grep -oP 'argv\[\]=\K[^;]*' | head -1 | sed -E 's/ +$//')
  [[ -n "$argv" ]] || { info "can't read ExecStart of $u - skipped"; continue; }
  argv=$(sed -E 's/ --fast( [a-z0-9_]+)*//; s/ --preview-method [a-z0-9]+//; s/ --reserve-vram [0-9.]+//' <<<"$argv")
  info "$u: $argv $FLAGS"
  write "/etc/systemd/system/$u.d/$DROPIN" <<EOF
[Service]
ExecStart=
ExecStart=$argv $FLAGS
EOF
done

say "B1: Ollama"
write "/etc/systemd/system/ollama.service.d/$DROPIN" <<EOF
[Service]
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF

say "B4: persistence mode and swappiness"
if (( DRY )); then info "[dry-run] nvidia-smi -pm 1; vm.swappiness=10"
else
  sudo nvidia-smi -pm 1 >/dev/null && info "persistence mode on"
  # survives reboots if the daemon exists
  systemctl list-unit-files nvidia-persistenced.service >/dev/null 2>&1 && sudo systemctl enable --now nvidia-persistenced >/dev/null 2>&1 || true
fi
write /etc/sysctl.d/90-mixai-gpu.conf <<EOF
vm.swappiness=10
EOF
(( DRY )) || sudo sysctl -q --system

(( DRY )) && { say "Dry run: nothing changed."; exit 0; }

say "Restarting ComfyUI and Ollama"
sudo systemctl daemon-reload
sudo systemctl restart "${UNITS[@]}" ollama
for p in 8188 8189; do
  printf '    :%s ' "$p"
  for _ in $(seq 1 90); do curl -sf -o /dev/null "http://127.0.0.1:$p/system_stats" && break; printf '.'; sleep 2; done
  curl -s "http://127.0.0.1:$p/system_stats" | python3 -c "import sys,json; print(' up:', ' '.join(json.load(sys.stdin)['system']['argv'][1:]))" 2>/dev/null || echo " NOT UP - see: journalctl -u 'comfyui*' -n 50"
done
info "Ollama: $(systemctl show -p Environment --value ollama | tr ' ' '\n' | grep -E 'KEEP_ALIVE|MAX_LOADED' | tr '\n' ' ')"
info "swappiness: $(cat /proc/sys/vm/swappiness)   persistence: $(nvidia-smi --query-gpu=persistence_mode --format=csv,noheader | tr '\n' ' ')"
say "Done. Undo any time with: ./scripts/tune-gpu.sh --undo"
