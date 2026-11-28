"""Settings -> ComfyUI -> Restart: admins only, and only a comfyui* unit systemd itself lists."""
import subprocess

import pytest

from app import comfy_services as cs
from tests.test_api_tokens import _user, login  # noqa: F401  (fixtures)

LIST = "comfyui.service loaded active running ComfyUI\ncomfyui-gpu1.service loaded failed failed ComfyUI 2\n"


@pytest.fixture
def systemd(monkeypatch):
    calls = []

    def run(args, timeout=20):
        calls.append(args)
        if args[:2] == ["systemctl", "list-units"]:
            return subprocess.CompletedProcess(args, 0, LIST, "")
        if args[:2] == ["systemctl", "show"]:
            port = "8189" if "gpu1" in args[-1] else "8188"
            return subprocess.CompletedProcess(args, 0, f"{{ path=/x ; argv[]=/x main.py --port {port} ; }}", "")
        return subprocess.CompletedProcess(args, monkeypatch.restart_rc, "", monkeypatch.restart_err)

    monkeypatch.restart_rc, monkeypatch.restart_err = 0, ""
    monkeypatch.setattr(cs.shutil, "which", lambda _: "/usr/bin/systemctl")
    monkeypatch.setattr(cs, "_run", run)
    return calls


def test_lists_units_with_their_card(client, systemd):
    units = client.get("/api/system/comfy-services").json()["units"]
    assert [(u["unit"], u["active"], u["port"]) for u in units] == [
        ("comfyui.service", "active", 8188), ("comfyui-gpu1.service", "failed", 8189)]


def test_restart_runs_sudo_for_that_unit_only(client, systemd, monkeypatch):
    r = client.post("/api/system/comfy-services/comfyui-gpu1.service/restart")
    assert r.status_code == 200
    assert systemd[-1] == ["sudo", "-n", "systemctl", "restart", "comfyui-gpu1.service"]
    # anything systemd didn't list is refused before sudo is ever asked
    n = len(systemd)
    for bad in ("mixai-api.service", "comfyui.service;reboot", "sshd.service"):
        assert client.post(f"/api/system/comfy-services/{bad}/restart").status_code == 409
    assert not any(c[0] == "sudo" for c in systemd[n:])


def test_without_the_sudoers_rule_it_says_what_to_run(client, systemd, monkeypatch):
    monkeypatch.restart_rc, monkeypatch.restart_err = 1, "sudo: a password is required"
    r = client.post("/api/system/comfy-services/comfyui.service/restart")
    assert r.status_code == 409 and "allow-comfy-restart.sh" in r.json()["detail"]


def test_not_for_non_admins(client, systemd, login):  # noqa: F811
    editor = login(_user("ed@test.local", "editor"))
    assert editor.get("/api/system/comfy-services").status_code in (403, 404)
    assert editor.post("/api/system/comfy-services/comfyui.service/restart").status_code in (403, 404)
    assert not any(c[0] == "sudo" for c in systemd)
