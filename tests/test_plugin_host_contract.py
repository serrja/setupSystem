"""Check the two trusted UI boundaries and explicit resumption of interrupted actions."""

import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pexpect
import pytest
from test_extensions import descriptor as manifest_fields
from test_extensions import wheel
from test_plugin_lifecycle import activation

from setup_system import authorization, plugin_manager
from setup_system.extensions import Activation
from setup_system.journal import Event, Journal
from setup_system.plugin_manager import PluginManager, Snapshot
from setup_system.plugin_ui import panel_type
from setup_system_sdk import ActionEvent, Manifest, Mutation, Outcome, Plan, Resource, Result
from setup_system_sdk.protocol import Reply


def make_panel(tmp_path, source, *, attribute="Panel", module="ext_one.view", permission=True):
    manifest = Manifest.model_validate(
        manifest_fields("one", permissions=["ui-code"] if permission else [])
    )
    entries = (
        "[setup_system.plugins]\none = ext_one:Backend\n[setup_system.plugin_ui]\none = "
        + module
        + ":"
        + attribute
        + "\n"
    )
    dist = wheel(
        tmp_path,
        "fixture",
        "1.0",
        "ext_one",
        "raise RuntimeError('backend imported')",
        manifest=manifest.model_dump(mode="json"),
        entries=entries,
        extras={"ext_one/view.py": source.encode()},
    )
    site = tmp_path / "site"
    with ZipFile(dist) as archive:
        archive.extractall(site)
    return Activation(
        Path(sys.executable),
        manifest,
        hashlib.sha256(dist.read_bytes()).hexdigest(),
        site / "ext_one",
    )


def test_ui_loads_only_approved_bytes_and_requires_restart(tmp_path):
    state = make_panel(
        tmp_path, "from setup_system_sdk.ui import PluginPanel\nclass Panel(PluginPanel): pass\n"
    )
    with pytest.raises(ValueError, match="Restart"):
        panel_type(state, None)
    panel = panel_type(state, state.digest)
    assert panel.__name__ == "Panel" and panel_type(state, state.digest) is panel
    assert not (state.resources / "__pycache__").exists()
    (state.resources / "view.py").write_text("raise RuntimeError('edited')")
    with pytest.raises(ValueError, match="differs"):
        panel_type(state, state.digest)


@pytest.mark.parametrize(
    ("source", "attribute", "module", "permission", "message"),
    [
        ("pass", "Panel", "ext_one.view", False, "approved"),
        ("pass", "Panel", "ext_bad.view", True, "separate"),
        ("raise ImportError('backend-only-package')", "Panel", "ext_one.view", True, "unavailable"),
        ("class Wrong: pass", "Wrong", "ext_one.view", True, "SDK"),
    ],
)
def test_unusable_ui_is_localized(tmp_path, source, attribute, module, permission, message):
    state = make_panel(tmp_path, source, attribute=attribute, module=module, permission=permission)
    with pytest.raises(ValueError, match=message):
        panel_type(state, state.digest)
    assert sys.path.count(str(state.resources.parent)) == 0


@pytest.mark.parametrize(
    ("matches", "echo", "response", "exitstatus", "expected"),
    [
        ([1], [], None, 0, "verified"),
        ([1], [], None, 1, "refused"),
        ([0, 1], [False, False], "demo", 0, "verified"),
        ([0, 0], [False, False], "demo", 1, "challenge"),
        ([0], [False], None, 1, "cancelled"),
        ([2], [], None, 1, "challenge"),
        ([0], [True], None, 0, "echo"),
        ([0], [False, True], "demo", 0, "echo"),
        ([3], [], None, 1, "unavailable"),
    ],
)
def test_native_authorization_challenges_use_only_echo_off_native_terminal(
    monkeypatch, matches, echo, response, exitstatus, expected
):
    sent = []
    closed = []

    class Terminal:
        async def expect(self, patterns, async_):
            match = matches.pop(0)
            if match == 3:
                raise pexpect.TIMEOUT("synthetic")
            return match

        def getecho(self):
            return echo.pop(0)

        def sendline(self, line):
            sent.append(line)

        def close(self, force=False):
            closed.append(force)

    terminal = Terminal()
    terminal.exitstatus = exitstatus
    monkeypatch.setattr(authorization, "protected", lambda path, directory: None)
    monkeypatch.setattr(authorization.pexpect, "spawn", lambda *a, **kw: terminal)
    monkeypatch.setattr(authorization.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="demo"))

    async def prompt():
        return authorization.BecomePassword(response) if response else None

    if expected == "echo":
        with pytest.raises(ValueError, match="echo"):
            asyncio.run(authorization.prepare_sudo("demo", prompt, 0.1))
        assert not sent
    else:
        message = asyncio.run(authorization.prepare_sudo("demo", prompt, 0.1))
        assert expected in message and "demo" not in message
    assert closed[-1] is True


def test_explicit_remaining_cleanup_never_replays_verified_deletions(home, monkeypatch):
    state = activation(home)
    action = state.manifest.actions[0].model_copy(update={"cleanup_phase": "local"})
    state.manifest.actions[:] = [action]
    manager = PluginManager(state_root=home / "state")
    left = Resource(kind="file", identity="left", ownership="created", cleanup=True, after="v")
    right = left.model_copy(update={"identity": "right"})
    created = [left, right]
    removals = [
        r.model_copy(update={"effect": "remove", "before": "v", "after": None}) for r in created
    ]
    remaining = {r.identity for r in removals}

    async def active(plugin):
        return state

    async def read(state, request, options):
        if request.method == "plan":
            return Reply(
                plan=Plan(
                    summary="Remove", resources=[r for r in removals if r.identity in remaining]
                )
            )
        assert request.method == "verify"
        gone = [r for r in request.plan.resources if r.identity not in remaining]
        return Reply(
            result=Result(
                outcome=Outcome.COMPLETE
                if len(gone) == len(request.plan.resources)
                else Outcome.PARTIAL,
                message="read",
                resources=gone,
            )
        )

    calls = []

    async def native(state, snap, password):
        requested = [r.identity for r in snap.mutation.plan.resources]
        calls.append(requested)
        if len(calls) == 1:
            remaining.remove("left")
            return Result(
                outcome=Outcome.PARTIAL,
                message="Paused",
                resources=[removals[0].model_copy(update={"verified": True})],
            )
        remaining.remove("right")
        return Result(
            outcome=Outcome.CHANGED, message="Removed", resources=snap.mutation.plan.resources
        )

    monkeypatch.setattr(manager, "activation", active)
    monkeypatch.setattr(plugin_manager, "query", read)
    monkeypatch.setattr(manager.runner, "execute", native)

    async def run():
        old = uuid4()
        await manager.ledger.prepare(
            old, "demo", "create", state.digest, Plan(summary="Create", resources=created)
        )
        await manager.ledger.finish(
            old, Result(outcome=Outcome.COMPLETE, message="Created", resources=created)
        )
        first = await manager.preview("demo", "configure", {}, {})
        assert (await manager.apply(first)).outcome is Outcome.PARTIAL
        (outstanding,) = await manager.remaining_cleanup("demo")
        assert calls == [["left", "right"]] and [
            r.identity for r in outstanding.mutation.plan.resources
        ] == ["right"]
        remaining.add("left")
        assert not await manager.remaining_cleanup("demo")
        remaining.remove("left")
        assert (await manager.resume_cleanup(first)).outcome is Outcome.BLOCKED
        assert (await manager.resume_cleanup(outstanding)).outcome is Outcome.COMPLETE
        assert calls == [["left", "right"], ["right"]]
        assert not await manager.ledger.pending("demo") and not await manager.ledger.holdings(
            "demo"
        )
        assert (await manager.resume_cleanup(outstanding)).outcome is Outcome.BLOCKED

    asyncio.run(run())


def test_authenticated_readback_requires_explicit_consent_and_original_release(home, monkeypatch):
    state = activation(home)
    state.manifest.actions[:] = [
        state.manifest.actions[0].model_copy(update={"permissions": ["root"], "read_only": True})
    ]
    manager = PluginManager(state_root=home / "state")
    row = Resource(
        kind="policy",
        identity="account",
        ownership="preexisting",
        before="present",
        after="present",
    )
    plan = Plan(summary="Inspect policy", resources=[row])
    snapshot = Snapshot(
        plugin_id="demo",
        digest=state.digest,
        mutation=Mutation(action_id="configure", settings={}, inputs={}, plan=plan),
    )

    async def active(plugin):
        return state

    async def read(*args):
        return Reply(
            result=Result(
                outcome=Outcome.NEEDS_USER_ACTION, message="Native policy inspection required"
            )
        )

    called = []

    async def execute(state, snap, password):
        called.append(snap)
        return Result(
            outcome=Outcome.COMPLETE,
            message="Native policy verified",
            resources=[row.model_copy(update={"verified": True})],
        )

    async def grant(action, snap):
        assert action.read_only and snap == snapshot
        return authorization.BecomePassword("fixture")

    monkeypatch.setattr(manager, "activation", active)
    monkeypatch.setattr(plugin_manager, "protected_resources", lambda state: home)
    monkeypatch.setattr(plugin_manager, "query", read)
    monkeypatch.setattr(manager.runner, "execute", execute)

    async def run():
        await manager.ledger.prepare(
            snapshot.operation_id,
            "demo",
            "configure",
            state.digest,
            plan,
            snapshot=snapshot.model_dump_json(),
        )
        assert (await manager.reconcile("demo"))[0].outcome is Outcome.NEEDS_USER_ACTION
        assert not called
        assert (await manager.reconcile("demo", grant))[0].outcome is Outcome.COMPLETE
        assert called == [snapshot] and not await manager.ledger.pending("demo")

    asyncio.run(run())


def test_exported_global_history_contains_only_public_event_fields(home):
    async def run():
        journal = Journal(home / "state/history")
        event = Event(
            plugin_id="demo",
            operation_id=uuid4(),
            action_id="inspect",
            phase="verified",
            message_code="readback",
            details={"resource_count": 1},
        )
        await journal.emit(event)
        path = await journal.export()
        assert path.stat().st_mode & 0o777 == 0o600
        assert Event.model_validate_json(path.read_text()) == event
        assert path != await journal.export()
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ActionEvent.model_validate(
                {
                    "phase": "applied",
                    "message_code": "resource_seen",
                    "details": {"authorization": "synthetic-redacted"},
                }
            )

    asyncio.run(run())


def test_async_commands_run_only_after_fresh_reviewed_plan(home):
    from setup_system_sdk import Inputs
    from setup_system_sdk.native import NativeBackend

    seen = []

    class Program(NativeBackend):
        inputs = dict.fromkeys(("install", "configure", "uninstall"), Inputs)

        async def plan(self, action_id, settings, inputs):
            return Plan(summary=action_id)

        async def install(self, settings, inputs, plan):
            seen.append("install")
            return Result(outcome=Outcome.CHANGED, message=plan.summary)

        async def configure(self, settings, inputs, plan):
            seen.append("configure")
            return Result(outcome=Outcome.CHANGED, message=plan.summary)

        async def uninstall(self, settings, inputs, plan):
            seen.append("uninstall")
            return Result(outcome=Outcome.CHANGED, message=plan.summary)

    async def run():
        plugin = Program()
        for key in plugin.inputs:
            req = Mutation(action_id=key, settings={}, inputs={}, plan=Plan(summary=key))
            assert (await plugin.apply_request(key, req)).message == key
            stale = req.model_copy(update={"plan": Plan(summary="stale")})
            assert (await plugin.apply_request(key, stale)).outcome is Outcome.BLOCKED
        assert seen == list(plugin.inputs)

    asyncio.run(run())
