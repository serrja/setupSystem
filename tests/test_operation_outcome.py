"""Inspect uncertain operations from a new application process without replaying them."""

import asyncio
from uuid import uuid4

from setup_system.resource_ledger import ResourceLedger
from setup_system_sdk import Outcome, Plan, Resource, Result


def test_read_only_lookups_create_no_storage(tmp_path):
    async def exercise():
        ledger = ResourceLedger(tmp_path / "state/resources.sqlite3")
        assert not await ledger.holdings("demo") and not await ledger.pending("demo")
        assert not list(tmp_path.iterdir())

    asyncio.run(exercise())


def test_partial_native_readback_survives_reopen_and_retains_unverified_reservation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))

    async def exercise():
        path = tmp_path / "state/resources.sqlite3"
        first = ResourceLedger(path)
        items = [
            Resource(kind="file", identity=n, after="original", ownership="created", cleanup=True)
            for n in ("one", "two")
        ]
        operation = uuid4()
        await first.prepare(
            operation,
            "demo",
            "configure",
            "a" * 64,
            Plan(summary="write files", resources=items),
            snapshot="fixture-snapshot",
        )
        await first.applied(operation)
        await first.finish(
            operation,
            Result(
                outcome=Outcome.PARTIAL,
                message="One readback",
                resources=[items[0].model_copy(update={"verified": True}), items[1]],
            ),
        )
        second = ResourceLedger(path)
        assert await second.pending("demo") == ["fixture-snapshot"]
        assert [r.identity for r in await second.holdings("demo")] == ["one"]
        await second.finish(
            operation, Result(outcome=Outcome.COMPLETE, message="Reconciled", resources=items)
        )
        assert not await second.pending("demo") and len(await second.holdings("demo")) == 2

    asyncio.run(exercise())
