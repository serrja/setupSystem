"""Verify multi-step cleanup and ownership against real committed file receipts."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_plugin_lifecycle import activation

from setup_system import cleanup
from setup_system.application.rollback import RecoveryPlan
from setup_system.configuration import Settings
from setup_system.domain.receipts import digest
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.plugin_manager import PluginManager, Snapshot
from setup_system.resource_ledger import ResourceLedger
from setup_system_sdk import Manifest, Mutation, Outcome, Plan, Resource, Result


def test_rollback_through_make_preserves_the_original_and_releases_only_its_holdings(
    home, monkeypatch
):
    manager = PluginManager(state_root=home / "ledger", env_root=home / "envs")
    state = activation(home)

    async def select(plugin):
        return state

    monkeypatch.setattr(manager, "activation", select)
    monkeypatch.setattr(cleanup, "removable", lambda *args: ("active.json",))
    retired = []
    monkeypatch.setattr(cleanup, "retire", lambda *args: retired.append(args))
    store = Settings()
    external = home / "author"
    own = home / "fragment"
    external.write_bytes(b"personal")

    async def publish(contents):
        holdings = await manager.ledger.holdings("demo")
        before = {path: observe(path) for path in contents}
        rows = [
            Resource(
                kind="file",
                identity=str(p),
                before=before[p].sha256 if before[p] else None,
                after=digest(raw),
                ownership="modified" if before[p] else "created",
                cleanup=before[p] is None,
            )
            for p, raw in contents.items()
        ]
        snapshot = Snapshot(
            plugin_id="demo",
            digest=state.digest,
            mutation=Mutation(
                action_id="configure",
                holdings=tuple(holdings),
                settings={},
                inputs={},
                plan=Plan(summary="Publish", resources=rows),
            ),
        )
        await manager.ledger.prepare(
            snapshot.operation_id,
            "demo",
            "configure",
            state.digest,
            snapshot.mutation.plan,
            snapshot=snapshot.model_dump_json(),
        )
        receipt = apply_files(contents, expected=before)
        await manager.ledger.applied(snapshot.operation_id, receipt.name)
        await manager.ledger.finish(
            snapshot.operation_id, Result(outcome=Outcome.COMPLETE, message="Read", resources=rows)
        )
        return str(snapshot.operation_id), receipt.name

    async def run():
        older = await publish({external: b"v1", own: b"original managed"})
        newer = await publish({external: b"v2"})
        assert await manager.ledger.recoveries("demo") == [newer, older]
        blocked = await manager.rollback("demo", older[0], RecoveryPlan.inspect(older[1]))
        assert blocked.outcome is Outcome.BLOCKED
        approved = await cleanup.preview_cleanup(manager, store, "demo", {})
        assert not approved.blockers and [r.operation for r in approved.recovery] == [
            newer[0],
            older[0],
        ]
        assert str(own) in approved.details() and str(external) in approved.details()
        own.write_bytes(b"manual edit")
        changed = await cleanup.preview_cleanup(manager, store, "demo", {})
        assert changed.blockers and not retired
        own.write_bytes(b"original managed")

        async def no_password(action):
            pytest.fail("File recovery cannot request native authorization")

        result = await cleanup.apply_cleanup(manager, store, approved, no_password)
        assert result.outcome is Outcome.COMPLETE
        assert external.read_bytes() == b"personal" and not own.exists()
        assert len(retired) == 1 and not await manager.ledger.holdings("demo")
        assert not await manager.ledger.recoveries("demo")

    asyncio.run(run())


def test_rollback_rejects_out_of_scope_paths_later_writer_and_other_consumers(home):
    ledger = ResourceLedger(home / "state/ledger")
    target = str(home / "history")
    row = Resource(kind="file", identity=target, ownership="created", after="first", cleanup=True)
    original = uuid4()
    receipt = "20261125T102030Z-abccddef"
    snap = Snapshot(
        plugin_id="alpha",
        digest="a" * 64,
        mutation=Mutation(
            action_id="configure",
            holdings=(),
            settings={},
            inputs={},
            plan=Plan(summary="Configure", resources=[row]),
        ),
    )

    async def run():
        await ledger.prepare(
            original,
            "alpha",
            "configure",
            "a" * 64,
            snap.mutation.plan,
            snapshot=snap.model_dump_json(),
        )
        await ledger.finish(
            original,
            Result(outcome=Outcome.COMPLETE, message="Saved", resources=[row], recovery_id=receipt),
        )
        with pytest.raises(ValueError, match="does not belong"):
            await ledger.reserve_rollback("other", str(original), receipt, {target})
        with pytest.raises(ValueError, match="outside"):
            await ledger.reserve_rollback("alpha", str(original), receipt, {str(home / "private")})
        shared = row.model_copy(update={"before": "first", "ownership": "shared", "cleanup": False})
        other = uuid4()
        await ledger.prepare(
            other, "beta", "inspect", "b" * 64, Plan(summary="Use", resources=[shared])
        )
        await ledger.finish(
            other, Result(outcome=Outcome.NO_CHANGES, message="Shared", resources=[shared])
        )
        with pytest.raises(ValueError, match="another plugin"):
            await ledger.reserve_rollback("alpha", str(original), receipt, {target})
        assert await ledger.holdings("alpha") == [row]

    asyncio.run(run())


@pytest.mark.parametrize(
    "overrides",
    [
        {"settings_schema": {"properties": {"credential": {"writeOnly": True}}}},
        {
            "settings_schema": {
                "properties": {"credential": {"ui": {"secret": True, "widget": "text"}}}
            }
        },
        {
            "settings_schema": {
                "$defs": {"Profile": {"properties": {"password": {"format": "password"}}}}
            }
        },
        {"settings_schema": {"items": {"ui": {"undeclared_privilege": True}}}},
        {"source": "https://example.test/manifest?token=fake"},
        {"source": "https://account:fixture@example.test/release"},
        {"source": "http://example.test"},
        {"author": "bad\x1bname"},
    ],
)
def test_untrusted_metadata_cannot_offer_secret_storage_or_control_the_consent_screen(
    home, overrides
):
    manifest = activation(home).manifest.model_dump()
    with pytest.raises(ValidationError):
        Manifest.model_validate(manifest | overrides)
