"""Verify scoped native registry logout, service transitions and owned context cleanup."""

import asyncio
import json

import setup_plugin_docker as app

from setup_system_sdk import Inputs, Mutation, Outcome, Resource, ownership_context
from setup_system_sdk.native import command as run_native


def test_registry_query_never_passes_auth_to_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".docker/config.json"
    cfg.parent.mkdir()
    cfg.write_text(
        '{"credsStore":"secretservice","auths":{"unrelated.test":{"auth":"fixture-secret"}}}'
    )
    present = {"yes": True}

    def cli(*argv, **kwargs):
        if argv[0] == "/usr/bin/jq":
            rc, data = run_native(*argv, **kwargs)
            assert "fixture-secret" not in data
            return rc, data
        if argv[0].endswith("docker-credential-secretservice"):
            assert argv[1:] == ("list",)
            return 0, json.dumps({"registry.test": "fixture"} if present["yes"] else {})
        assert argv == ("/usr/bin/docker", "logout", "registry.test")
        present["yes"] = False
        return 0, ""

    monkeypatch.setattr(app, "command", cli)
    monkeypatch.setattr(app.registry, "command", cli)
    plugin = app.Plugin()
    data = app.Registry(host="registry.test")
    settings = app.Settings()
    plan = plugin.prepare("logout", settings, data)
    assert plan.outcome == Outcome.READY
    outcome = asyncio.run(
        plugin.apply_request(
            "logout", Mutation(action_id="logout", inputs=data.model_dump(), settings={}, plan=plan)
        )
    )
    assert (
        outcome.outcome == Outcome.CHANGED
        and asyncio.run(plugin.verify(plan, settings, data)).outcome == Outcome.COMPLETE
    )
    assert plugin.prepare("logout", settings, data).outcome == Outcome.NO_CHANGES
    cfg.write_text('{"auths":{"registry.test":{"auth":"fixture-secret"}}}')
    assert plugin.prepare("logout", settings, data).outcome == Outcome.NEEDS_USER_ACTION


def test_ownership_readback_and_service_control(monkeypatch):
    plugin = app.Plugin()
    settings = app.Settings()
    host = app.endpoint(settings)
    contexts = {"managed": host}
    active = {"context": "default", "state": "inactive"}

    def cli(*argv, **kw):
        tail = argv[1:]
        if "systemctl" in argv[0]:
            if "--value" in tail:
                return 0, active["state"]
            if "show" in tail:
                return 0, "LoadState=loaded\nActiveState=" + active["state"]
            active["state"] = "active" if "start" in tail else "inactive"
        elif tail[:2] == ("context", "rm"):
            contexts.pop(tail[2])
        elif tail[:2] == ("context", "show"):
            return 0, active["context"]
        elif tail[:2] == ("context", "ls"):
            return 0, "\n".join(contexts)
        elif tail[:2] == ("context", "inspect"):
            return 0, contexts[tail[2]]
        return 0, ""

    monkeypatch.setattr(app, "command", cli)
    monkeypatch.setattr(app.configuration, "command", cli)
    for action in ("start-service", "stop-service"):
        plan = plugin.prepare(action, settings, Inputs())
        assert plugin.mutate(action, settings, Inputs(), plan).outcome == Outcome.CHANGED
        assert asyncio.run(plugin.verify(plan, settings, Inputs())).outcome == Outcome.COMPLETE
    row = Resource(
        kind="docker-context", identity="managed", ownership="created", after=host, cleanup=True
    )
    selection = app.configuration.RemoveContexts()
    with ownership_context((row,)):
        active["context"] = "managed"
        assert (
            plugin.prepare("remove-owned-contexts", settings, selection).outcome == Outcome.BLOCKED
        )
        active["context"] = "default"
        plan = plugin.prepare("remove-owned-contexts", settings, selection)
        assert plan.resources[0].effect == "remove"
        assert (
            plugin.mutate("remove-owned-contexts", settings, selection, plan).outcome
            == Outcome.CHANGED
        )
        assert asyncio.run(plugin.verify(plan, settings, selection)).outcome == Outcome.COMPLETE


def test_native_credential_helper_inherits_only_the_validated_user_bus(monkeypatch, tmp_path):
    import socket
    import subprocess

    from setup_system_sdk import native

    folder = tmp_path / "runtime"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(folder))
    monkeypatch.setenv("BASH_ENV", "ignored")
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(native, "run_command", run)
    with socket.socket(socket.AF_UNIX) as bus:
        # Relative bind avoids the kernel's 108-byte sockaddr limit under a long pytest root.
        monkeypatch.chdir(folder)
        bus.bind("bus")
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(folder / "bus"))
        native.command("/usr/bin/docker-credential-secretservice", "list")
        assert seen[-1]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=" + str(folder / "bus")
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "tcp:host=other.invalid")
        native.command("/usr/bin/docker-credential-secretservice", "list")
        assert "DBUS_SESSION_BUS_ADDRESS" not in seen[-1] and "BASH_ENV" not in seen[-1]
