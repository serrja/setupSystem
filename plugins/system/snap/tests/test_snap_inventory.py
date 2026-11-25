"""Read existing snapd revisions without taking ownership or mutating the workstation."""

import asyncio

import pytest
import setup_plugin_snap as snap

from setup_system_sdk import Inputs, Outcome, PluginSettings


def test_list_preserves_installed_active_and_disabled_revisions(monkeypatch):
    table = (
        "Name Version Rev Tracking Publisher Notes\n"
        "app 2.0 20 latest/stable team -\napp 1.5 17 latest/stable team disabled\n"
    )

    def cli(*args, capture=False):
        assert (
            args == ("/usr/bin/snap", "list", "--all", "--color=never", "--unicode=never")
            and capture
        )
        return 0, table

    monkeypatch.setattr(snap, "command", cli)
    monkeypatch.setattr(snap, "installed", lambda name: "1")
    plugin = snap.Plugin()
    inspected = asyncio.run(plugin.inspect(PluginSettings()))
    assert inspected.outcome == Outcome.NO_CHANGES
    rows = plugin.prepare("list-installed", PluginSettings(), Inputs()).resources
    assert rows == inspected.resources and [r.identity for r in rows] == ["app@20", "app@17"]
    assert all(r.ownership == "preexisting" and not r.cleanup for r in rows)
    assert plugin.prepare("uninstall", PluginSettings(), Inputs()).outcome == Outcome.BLOCKED


@pytest.mark.parametrize(
    "reply",
    [
        (1, ""),
        (0, "different header"),
        (0, "Name Version Rev Tracking Publisher Notes\nincomplete"),
    ],
)
def test_broken_inventory_cannot_authorize_removal(monkeypatch, reply):
    monkeypatch.setattr(snap, "command", lambda *a, **k: reply)
    monkeypatch.setattr(snap, "installed", lambda name: "1")
    assert snap.inspection().outcome == Outcome.NEEDS_USER_ACTION
    assert snap.Plugin().prepare("uninstall", PluginSettings(), Inputs()).outcome == Outcome.BLOCKED


@pytest.mark.parametrize(
    "output", ["", "No snaps are installed yet. Try 'snap install hello-world'."]
)
def test_empty_snapd_without_credentials(monkeypatch, output):
    monkeypatch.setattr(snap, "command", lambda *args, **kw: (0, output))
    assert snap.inventory() == []


@pytest.mark.parametrize("occupied", ["snaps", "snapshots", "changes", None])
def test_runtime_guard_queries_local_daemon(monkeypatch, occupied):
    import stat
    from types import SimpleNamespace

    import httpx
    from setup_plugin_snap import removal_guard as guard

    class Socket:
        def lstat(self):
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFSOCK)

        def resolve(self):
            return self

    monkeypatch.setattr(guard, "SOCKET", Socket())
    original = httpx.Client
    paths = []

    def client(**kwargs):
        def respond(request):
            resource = request.url.path.rsplit("/", 1)[-1]
            paths.append(resource)
            assert request.url.host == "localhost"
            assert (
                request.url.query
                == {"snaps": b"select=all", "snapshots": b"", "changes": b"select=in-progress"}[
                    resource
                ]
            )
            return httpx.Response(
                200, json={"type": "sync", "result": [{"id": 42}] if resource == occupied else []}
            )

        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "Client", client)
    if occupied:
        with pytest.raises(ValueError, match="Retain snapd"):
            guard.require_empty()
        assert paths[-1] == occupied
    else:
        guard.require_empty()
        assert paths == ["snaps", "snapshots", "changes"]


def test_pre_dpkg_guard_rereads_protected_plugin_after_actual_closure(tmp_path, monkeypatch):
    import io
    import subprocess
    from pathlib import Path

    from pydantic import ValidationError

    from setup_system_sdk import LifecycleAction, Plan, Resource, apt

    root = tmp_path / "installed"
    folder = root / "plugins/snap/setup_plugin_snap"
    folder.mkdir(parents=True)
    (folder / "apt.json").write_text('{"names":["snapd"],"removal_guard":"removal_guard.py"}')
    guard = folder / "removal_guard.py"
    guard.write_text("raise SystemExit(23)\n")
    stage = tmp_path / "setup-system-pkg-fixture"
    stage.mkdir()
    row = Resource(
        kind="package",
        identity="snapd",
        ownership="created",
        before="1",
        effect="remove",
        cleanup=True,
    )
    approval = apt.Approval(
        plugin_id="snap",
        action_id=LifecycleAction.UNINSTALL,
        plan=Plan(summary="Remove snapd", resources=[row]),
    )
    receipt = stage / "approval.json"
    receipt.write_text(approval.model_dump_json())
    monkeypatch.setattr(apt, "INSTALLED", root)
    monkeypatch.setattr(apt, "Path", lambda value: tmp_path if value == "/run" else Path(value))
    monkeypatch.setattr(apt.os, "geteuid", lambda: 0)
    monkeypatch.setattr(apt, "current_target", lambda: ("amd64", True))
    checked = []

    def installed_file(path, relative):
        assert path == root / relative and path.parent == folder and path.is_file()
        checked.append(path)

    monkeypatch.setattr(apt, "require_installed_resource", installed_file)
    monkeypatch.setattr(apt, "protected", lambda p, directory: p == receipt and not directory)

    def stream(package):
        return io.StringIO(f"VERSION 3\n\n{package} 1 amd64 0 > - - 0 **REMOVE**\n")

    monkeypatch.setattr(apt.sys, "stdin", stream("snapd"))
    with pytest.raises(subprocess.CalledProcessError) as error:
        apt.check(stage)
    assert error.value.returncode == 23
    assert error.value.cmd == ["/usr/bin/python3", "-I", "-B", str(guard)]
    assert checked == [folder / "apt.json", guard]

    checked.clear()
    monkeypatch.setattr(apt.sys, "stdin", stream("unrelated"))
    with pytest.raises(ValueError, match="Unapproved"):
        apt.check(stage)
    assert guard not in checked
    for invalid in ("../../other.py", "/tmp/foreign.py", "guard.py\n", "guard.sh"):
        with pytest.raises(ValidationError):
            apt.Packages(names=("snapd",), removal_guard=invalid)
