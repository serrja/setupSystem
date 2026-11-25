"""Exercise confirmed plugin operations with private state and real Make subprocesses."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from setup_system import plugin_manager
from setup_system.configuration import Settings
from setup_system.extensions import Activation
from setup_system.journal import Event, Journal
from setup_system.plugin_manager import AsyncMakeRunner, PluginManager, Snapshot
from setup_system.resource_ledger import ResourceLedger
from setup_system_sdk import Action, Manifest, Mutation, Outcome, Plan, Resource, Result
from setup_system_sdk.protocol import Reply


def activation(root):
    action = Action(id="configure", label="Configure", target="write")
    return Activation(
        Path(sys.executable),
        Manifest(
            id="demo",
            name="Demo",
            author="Fixture",
            category="applications",
            kind="application",
            sdk=">=1,<2",
            version="1.0",
            source="https://example.invalid",
            entry_point="fixture:Plugin",
            actions=[action],
            permissions=[],
            capabilities=[],
            dependencies=[],
            settings_schema={},
            input_schemas={"configure": {}},
        ),
        "a" * 64,
        root,
    )


def resource(before=None, after="one", **kwargs):
    return Resource(
        kind="file",
        identity="example",
        before=before,
        after=after,
        ownership="modified" if before else "created",
        cleanup=True,
        **kwargs,
    )


def test_real_make_transmits_data_and_ignores_caller_control(home, monkeypatch):
    value = "-$ 'quote' \" ${symbol} $(shell anything)\n資料"
    state = activation(home)
    task = home / "Makefile"
    task.write_text('write:\n\t@"$' + '${SETUP_SYSTEM_INTERPRETER}" -I -B worker.py\n')
    (home / "worker.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "assert not set(os.environ) & {'MAKEFILES','BASH_ENV','PYTHONPATH',"
        "'LD_PRELOAD','ANSIBLE_CONFIG'}\n"
        "request=json.load(sys.stdin);Path('seen').write_text(request['inputs']['name'])\n"
        "print(json.dumps({'outcome':'changed','message':'Published'}))\n"
    )
    for name in ("MAKEFILES", "BASH_ENV", "PYTHONPATH", "LD_PRELOAD", "ANSIBLE_CONFIG"):
        monkeypatch.setenv(name, "not-forwarded")
    plan = Plan(summary="Ready")
    snapshot = Snapshot(
        digest=state.digest,
        plugin_id="demo",
        mutation=Mutation(action_id="configure", settings={}, inputs={"name": value}, plan=plan),
    )
    result = asyncio.run(AsyncMakeRunner(Settings.application()).execute(state, snapshot, None))
    assert result.outcome == Outcome.CHANGED and (home / "seen").read_text() == value


def test_ledger_conflict_prevents_removing_shared_or_modified_resources(home):
    async def exercise():
        ledger = ResourceLedger(home / "state/ledger.db")
        written = resource()
        first = uuid4()
        await ledger.prepare(
            first, "alpha", "create", "a" * 64, Plan(summary="Create", resources=[written])
        )
        await ledger.applied(first)
        await ledger.finish(
            first, Result(outcome=Outcome.COMPLETE, message="Verified", resources=[written])
        )
        changed = resource(before="manual", after=None, effect="remove")
        with pytest.raises(ValueError, match="unchanged"):
            await ledger.prepare(
                uuid4(), "alpha", "remove", "a" * 64, Plan(summary="Delete", resources=[changed])
            )
        shared = written.model_copy(
            update={"ownership": "shared", "before": "one", "cleanup": False}
        )
        second = uuid4()
        await ledger.prepare(
            second, "beta", "use", "b" * 64, Plan(summary="Use", resources=[shared])
        )
        await ledger.finish(
            second, Result(outcome=Outcome.COMPLETE, message="Verified", resources=[shared])
        )
        with pytest.raises(ValueError, match="Another plugin"):
            await ledger.prepare(
                uuid4(), "alpha", "remove", "a" * 64, Plan(summary="Delete", resources=[changed])
            )
        assert await ledger.holdings("alpha") == [written]
        assert await ledger.holdings("beta") == [shared]

    asyncio.run(exercise())


def test_failed_readback_never_creates_ownership_or_retries(home, monkeypatch):
    state = activation(home)
    plan = Plan(summary="Fixture", resources=[resource()])
    manager = PluginManager(state_root=home / "state")
    calls = []

    async def select(_):
        return state

    async def query(_, request, options):
        if request.method == "verify":
            raise ValueError("synthetic sensitive diagnostic")
        return Reply(plan=plan)

    async def execute(*args):
        calls.append("write")
        await asyncio.sleep(0.02)
        return Result(outcome=Outcome.CHANGED, message="Submitted", resources=plan.resources)

    monkeypatch.setattr(manager, "activation", select)
    monkeypatch.setattr(plugin_manager, "query", query)
    monkeypatch.setattr(manager.runner, "execute", execute)

    async def exercise():
        snapshot = await manager.preview("demo", "configure", {}, {})
        a, b = await asyncio.gather(manager.apply(snapshot), manager.apply(snapshot))
        assert a == b and a.outcome == Outcome.UNKNOWN_OUTCOME
        assert (await manager.apply(snapshot)).outcome == Outcome.BLOCKED
        assert await manager.ledger.holdings("demo") == []
        assert calls == ["write"]
        events = await manager.journal.history("demo")
        assert events[-1].outcome == Outcome.UNKNOWN_OUTCOME
        assert "sensitive" not in (home / "state/history/events.jsonl").read_text()

    asyncio.run(exercise())


def test_approved_plan_and_input_rechecked_before_mutation(home, monkeypatch):
    state = activation(home)
    manager = PluginManager(state_root=home / "state")
    values = iter(["old", "new"])

    async def select(_):
        return state

    async def query(*args):
        return Reply(plan=Plan(summary=next(values)))

    monkeypatch.setattr(manager, "activation", select)
    monkeypatch.setattr(plugin_manager, "query", query)

    async def exercise():
        snapshot = await manager.preview("demo", "configure", {}, {})
        assert (await manager.apply(snapshot)).outcome == Outcome.BLOCKED
        assert not (home / "state/resources.sqlite3").exists()

    asyncio.run(exercise())


def test_history_is_bounded_and_plugin_filtered(home):
    async def exercise():
        journal = Journal(home / "events", limit=1050)
        for index in range(14):
            await journal.emit(
                Event(
                    plugin_id="alpha" if index % 2 else "beta",
                    action_id="configure",
                    operation_id=uuid4(),
                    phase="planned",
                    message_code="planned",
                )
            )
        records = await journal.history()
        assert {e.plugin_id for e in records} == {"alpha", "beta"}
        assert len(records) < 14
        assert all(e.plugin_id == "alpha" for e in await journal.history("alpha"))
        assert all(p.stat().st_size <= 1050 for p in journal.directory.glob("*.jsonl"))

    asyncio.run(exercise())
