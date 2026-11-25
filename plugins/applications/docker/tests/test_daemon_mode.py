"""Use generated CLI clients and local fixture sockets to exercise Docker mode selection."""

import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
from setup_plugin_docker import Plugin, mode

from setup_system.application.rollback import RecoveryPlan
from setup_system_sdk import Mutation, Outcome, ownership_context


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("DATA", "CONFIG", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "docker.sock"
    daemon = socket.socket(socket.AF_UNIX)
    daemon.bind(local.name)
    local.chmod(0o660)
    monkeypatch.setattr(mode, "socket_path", lambda settings: local)
    unit = tmp_path / "systemctl"
    unit.write_text('#!/bin/sh\nexec /bin/cat "$HOME/unit"\n')
    unit.chmod(0o700)
    monkeypatch.setattr(mode, "SYSTEMCTL", str(unit))
    (tmp_path / "unit").write_text("LoadState=loaded\nActiveState=active\n")
    client = tmp_path / "fixture-docker"
    client.write_text(
        """#!/usr/bin/python3 -I
import json, sys
from pathlib import Path
r = Path(__file__).parent
args = sys.argv[1:]
host = (r/'host').read_text()
if args[:2] == ['context', 'ls']:
    print('default\\t' + host + '\\nrootless\\t' + host + '\\nremote\\ttcp://other.invalid')
elif args[:2] == ['context', 'inspect']:
    print(host)
elif args[0] == '--host' and args[2] == 'info':
    if ".ID" in args[-1]:
        print((r/'engine').read_text())
    if ".SecurityOptions" in args[-1]:
        print((r/'permissions').read_text())
else:
    print(json.dumps(args))
"""
    )
    client.chmod(0o700)
    monkeypatch.setattr(mode, "DOCKER", str(client))
    launcher = tmp_path / "fixture-launcher.sh"
    launcher.write_text(mode.LAUNCHER_SOURCE.read_text().replace("/usr/bin/docker", str(client)))
    monkeypatch.setattr(mode, "LAUNCHER_SOURCE", launcher)
    (tmp_path / "host").write_text(mode.endpoint(mode.Settings()))
    (tmp_path / "engine").write_text("aaaa-bbbb-cccc")
    (tmp_path / "permissions").write_text('["name=rootless"]')
    try:
        yield tmp_path
    finally:
        daemon.close()


@pytest.mark.parametrize("flavor", ["rootless", "rootful"])
def test_review_publish_readback_repeat_and_rollback_without_changing_global_context(
    engine, monkeypatch, flavor
):
    path = engine / ".local/bin/setup-system-docker"
    profile = engine / ".docker/config.json"
    profile.parent.mkdir()
    profile.write_text('{"currentContext":"production","credsStore":"fixture"}')
    saved = profile.read_bytes()
    (engine / "host").write_text(mode.endpoint(mode.Settings(mode=flavor)))
    if flavor == "rootful":
        real = Path.lstat
        node = engine / "docker.sock"

        def owner(item, *args, **kwargs):
            info = real(item, *args, **kwargs)
            if item != node:
                return info
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)

        monkeypatch.setattr(Path, "lstat", owner)
        (engine / "permissions").write_text('["name=seccomp,profile=builtin"]')
    plugin = Plugin()
    settings = mode.Settings(mode=flavor)
    selection = mode.DaemonMode()
    args = {"settings": settings.model_dump(), "inputs": selection.model_dump()}
    plan = plugin.prepare("setup-mode", settings, selection)
    assert plan.outcome == Outcome.READY and len(plan.resources) == 2 and not path.exists()
    assert {r.identity: r.permissions for r in plan.resources} == {
        str(path): 0o700,
        str(engine / "config/setup-system/docker/selected-daemon.json"): 0o600,
    }
    assert "aaaa-bbbb-cccc" in plan.summary and bool(plan.warnings) == (flavor == "rootful")
    result = asyncio.run(
        plugin.apply_request("setup-mode", Mutation(action_id="setup-mode", plan=plan, **args))
    )
    assert result.outcome == Outcome.CHANGED and result.recovery_id
    assert asyncio.run(plugin.verify(plan, settings, selection)).outcome == Outcome.COMPLETE
    assert plugin.prepare("setup-mode", settings, selection).outcome == Outcome.NO_CHANGES
    executed = subprocess.run([path, "version", "Unicode пример"], capture_output=True, check=True)
    assert json.loads(executed.stdout) == [
        "--host",
        (engine / "host").read_text(),
        "version",
        "Unicode пример",
    ]
    for options in (
        ["--host=tcp://other.invalid", "version"],
        ["-D", "--context", "other", "version"],
        ["--log-level", "debug", "-Hbad", "version"],
        ["-Dcother", "version"],
    ):
        refused = subprocess.run([path, *options], capture_output=True, check=False)
        assert refused.returncode == 2 and b"different endpoint" in refused.stderr
    container_args = ["--log-level", "info", "run", "image", "-H", "value", "--context=inside"]
    # The generated CLI returns its complete argv. An image's flags stay untouched.
    passed = subprocess.run([path, *container_args], capture_output=True, check=True)
    assert json.loads(passed.stdout)[2:] == container_args
    original = (engine / "permissions").read_text()
    (engine / "permissions").write_text("[]" if flavor == "rootless" else '["name=rootless"]')
    refused = subprocess.run([path, "version"], capture_output=True, check=False)
    assert refused.returncode and b"daemon mode changed" in refused.stderr
    (engine / "permissions").write_text(original)
    (engine / "engine").write_text("dddd-eeee-ffff")
    assert asyncio.run(plugin.verify(plan, settings, selection)).outcome == Outcome.PARTIAL
    stopped = subprocess.run([path, "version"], capture_output=True, check=False)
    assert stopped.returncode and b"Engine changed" in stopped.stderr
    (engine / "host").write_text("tcp://external.invalid")
    assert plugin.prepare("setup-mode", settings, selection).outcome == Outcome.BLOCKED
    stopped = subprocess.run([path, "version"], capture_output=True, check=False)
    assert stopped.returncode and b"context changed" in stopped.stderr
    assert RecoveryPlan.inspect(result.recovery_id).apply().outcome == Outcome.COMPLETE
    assert not path.exists() and profile.read_bytes() == saved


def test_local_edits_socket_replacement_and_stale_daemon_do_not_get_applied(engine):
    plugin = Plugin()
    settings = mode.Settings()
    selection = mode.DaemonMode(context="rootless")
    options = asyncio.run(plugin.choices("mode_contexts", "root", settings))
    assert options == ["rootless"]
    plan = plugin.prepare("setup-mode", settings, selection)
    (engine / "engine").write_text("dddd-eeee-ffff")
    mutation = Mutation(
        action_id="setup-mode",
        plan=plan,
        settings=settings.model_dump(),
        inputs=selection.model_dump(),
    )
    blocked = asyncio.run(plugin.apply_request("setup-mode", mutation))
    assert blocked.outcome == Outcome.BLOCKED
    path = engine / ".local/bin/setup-system-docker"
    path.parent.mkdir(parents=True)
    path.write_text("manual")
    path.chmod(0o700)
    with ownership_context(tuple(plan.resources)):
        assert plugin.prepare("setup-mode", settings, selection).outcome == Outcome.BLOCKED
    assert path.read_text() == "manual"
    (engine / "docker.sock").unlink()
    assert plugin.prepare("setup-mode", settings, selection).outcome == Outcome.NEEDS_USER_ACTION
    (engine / "docker.sock").write_bytes(b"personal file")
    assert plugin.prepare("setup-mode", settings, selection).outcome == Outcome.BLOCKED


def test_mode_checks_actual_service_subordinate_ids_and_engine_security(engine, monkeypatch):
    plugin = Plugin()
    req = mode.DaemonMode()
    settings = mode.Settings()
    (engine / "permissions").write_text("[]")
    assert plugin.prepare("setup-mode", settings, req).outcome == Outcome.BLOCKED
    (engine / "unit").write_text("LoadState=loaded\nActiveState=inactive\n")
    assert "separate Start" in plugin.prepare("setup-mode", settings, req).summary
    (engine / "unit").write_text("LoadState=not-found\nActiveState=inactive\n")
    real = mode.command

    def missing(*args, capture=False):
        if args[0] == "/usr/bin/getsubids":
            return (0, "0: user 300000 65536") if "-g" in args else (0, "0: user 100000 512")
        return real(*args, capture=capture)

    monkeypatch.setattr(mode, "command", missing)
    proposal = plugin.prepare("setup-mode", settings, req)
    assert (
        proposal.outcome == Outcome.NEEDS_USER_ACTION
        and "65536 subordinate subuid" in proposal.summary
    )
    assert (
        "65536 subordinate subgid" not in proposal.summary
        and "setuptool.sh install" in proposal.summary
    )
    assert (
        plugin.prepare("setup-mode", mode.Settings(mode="rootful"), req).outcome
        == Outcome.NEEDS_USER_ACTION
    )
    assert not (engine / ".local/bin").exists()


def test_missing_json_reader_is_reported_before_publication(engine, monkeypatch):
    check = os.access
    monkeypatch.setattr(
        mode.os, "access", lambda path, flags: path != "/usr/bin/jq" and check(path, flags)
    )
    plan = mode.prepare(mode.Settings(), mode.DaemonMode())
    assert plan.outcome == Outcome.NEEDS_USER_ACTION and "jq" in plan.summary
    assert not plan.resources and not (engine / ".local/bin/setup-system-docker").exists()
