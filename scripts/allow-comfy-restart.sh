#!/usr/bin/env bash
# Lets the app restart ComfyUI from Settings → ComfyUI, and nothing else: one sudoers rule allowing the app's
# user to run exactly `systemctl restart <unit>` for each comfyui*.service. Run once; again after adding a
# ComfyUI service.
#
#   ./scripts/allow-comfy-restart.sh            # writes /etc/sudoers.d/jafat-comfy-restart
#   ./scripts/allow-comfy-restart.sh --remove   # takes it away again
set -euo pipefail

DEST=/etc/sudoers.d/jafat-comfy-restart

if [[ "${1:-}" == "--remove" ]]; then
  sudo rm -f "$DEST" && echo "removed $DEST"
  exit 0
fi

APP_USER="${APP_USER:-$(systemctl show -p User --value mixai-api.service 2>/dev/null || true)}"
APP_USER="${APP_USER:-gpu}"
SYSTEMCTL="$(command -v systemctl)"
mapfile -t UNITS < <(systemctl list-unit-files --type=service --no-legend 'comfyui*' 2>/dev/null \
  | awk '{print $1}' | grep -v '@\.service$' || true)
(( ${#UNITS[@]} )) || { echo "no comfyui*.service units found" >&2; exit 1; }

cmds=""
for u in "${UNITS[@]}"; do cmds+="${cmds:+, }$SYSTEMCTL restart $u"; done
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '# scripts/allow-comfy-restart.sh: the app may restart ComfyUI, nothing else\n%s ALL=(root) NOPASSWD: %s\n' \
  "$APP_USER" "$cmds" > "$tmp"

# a broken sudoers file can lock sudo out, so it's checked before it goes in
sudo visudo -cf "$tmp" >/dev/null
sudo install -m 0440 -o root -g root "$tmp" "$DEST"
echo "wrote $DEST:"
sed -n 2p "$DEST"
if sudo -u "$APP_USER" sudo -n -l "$SYSTEMCTL" restart "${UNITS[0]}" >/dev/null 2>&1; then
  echo "check: $APP_USER may restart ${UNITS[*]}"
else
  echo "check failed: $APP_USER still can't restart ${UNITS[0]} without a password" >&2
  exit 1
fi
