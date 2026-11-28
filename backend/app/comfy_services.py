"""Restart ComfyUI from Settings (admins only).

The API runs as an ordinary user, so it asks sudo for exactly `systemctl restart <unit>` on the comfyui*
units and nothing else; scripts/allow-comfy-restart.sh installs that one sudoers rule. Without it the
button explains what to run instead of failing silently.
"""
import re
import shutil
import subprocess
from urllib.parse import urlparse

from app.config import get_settings

UNIT_RE = re.compile(r"^comfyui[\w@.-]*\.service$")
NOT_ALLOWED = ("This server doesn't let the app restart ComfyUI yet. On the server run, once: "
               "./scripts/allow-comfy-restart.sh")


class RestartError(Exception):
    pass


def _run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _port(unit: str) -> int | None:
    try:
        r = _run(["systemctl", "show", "-p", "ExecStart", "--value", unit])
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"--port[ =](\d+)", r.stdout)
    return int(m.group(1)) if m else None


def _card(port: int | None) -> str:
    if port is None:
        return ""
    s = get_settings()
    for lane in ("image", "video"):
        if any(urlparse(u).port == port for u in s.comfy_urls_for(lane)):
            return lane
    return ""


def units() -> list[dict]:
    if shutil.which("systemctl") is None:
        return []
    try:
        r = _run(["systemctl", "list-units", "--all", "--type=service", "--plain", "--no-legend", "comfyui*"])
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts or not UNIT_RE.match(parts[0]):
            continue
        port = _port(parts[0])
        # columns: UNIT LOAD ACTIVE SUB DESCRIPTION
        out.append({"unit": parts[0], "active": parts[2] if len(parts) > 2 else "unknown",
                    "state": parts[3] if len(parts) > 3 else "", "port": port, "card": _card(port)})
    return out


def restart(unit: str) -> None:
    # only a unit systemd itself lists, never a name straight from the request
    if not UNIT_RE.match(unit) or unit not in {u["unit"] for u in units()}:
        raise RestartError("No such ComfyUI service")
    try:
        r = _run(["sudo", "-n", "systemctl", "restart", unit], timeout=120)
    except subprocess.TimeoutExpired:
        raise RestartError("The restart took more than two minutes; check the server") from None
    except OSError as e:
        raise RestartError(f"Couldn't run the restart: {e}") from None
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if "password" in err.lower() or "not allowed" in err.lower() or "sudoers" in err.lower():
            raise RestartError(NOT_ALLOWED)
        raise RestartError(err[:300] or f"systemctl exited with {r.returncode}")
