from uuid import uuid4

import pytest
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.operations import (
    Executor,
    Plan,
    Receipt,
    Result,
    Snapshot,
    StepPlan,
)
from setup_system_sdk.resources import Resource
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from setup_system.application.ledger import LedgerService
from setup_system.domain.operations import StepEvidence
from setup_system.infrastructure.database.uow import UnitOfWork


def confirmation(plugin: str) -> Snapshot:
    resource = Resource(
        kind="file",
        identity="fixture",
        ownership=ResourceOwnership.CREATED,
        after="new",
        cleanup=True,
    )
    executor = Executor(
        plugin_id=plugin, version="1.0", release_digest="b" * 64, generation="fixture"
    )
    step = StepPlan(
        step_key="config",
        executor=executor,
        handler="publish",
        contract_version="1",
        resources=(resource,),
    )
    return Snapshot(
        operation_id=uuid4(),
        executor=executor,
        action_id="publish",
        settings_revision="1",
        settings={},
        inputs={},
        plan=Plan(summary="Publish fixture", resources=[resource], steps=(step,)),
    )


@pytest.mark.asyncio
async def test_conflicting_reservation_rolls_back_prepare(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ledger = LedgerService(UnitOfWork(sessions))
    first, second = confirmation("first"), confirmation("second")
    await ledger.prepare(first)
    with pytest.raises(IntegrityError):
        await ledger.prepare(second)
    async with ledger.uow.read() as scope:
        assert not await scope.operations.history("second")
        assert not await scope.operations.steps(str(second.operation_id))


@pytest.mark.asyncio
async def test_lost_reply_requires_readback(sessions: async_sessionmaker[AsyncSession]) -> None:
    ledger = LedgerService(UnitOfWork(sessions))
    snapshot = confirmation("fixture")
    (step,) = snapshot.plan.steps
    key = snapshot.operation_id
    await ledger.prepare(snapshot)
    await ledger.mark_running(key, step)
    with pytest.raises(ValueError):
        await ledger.finish(snapshot, Outcome.CANCELLED)
    await ledger.record_verified(
        key, step, Result(outcome=Outcome.UNKNOWN_OUTCOME, message="Lost reply")
    )
    await ledger.finish(snapshot, Outcome.UNKNOWN_OUTCOME)
    async with ledger.uow.read() as scope:
        assert not await scope.resources.list_holdings("fixture")
        (row,) = await scope.operations.steps(str(key))
        assert row.phase == "unresolved" and StepEvidence.decode(row.receipt).applied is None
    proof = Result.observed(
        outcome=Outcome.CHANGED, message="Inspected file", resources=list(step.resources)
    )
    await ledger.record_verified(key, step, proof)
    await ledger.finish(snapshot, Outcome.COMPLETE)
    async with ledger.uow.read() as scope:
        assert [r.identity for r in await scope.resources.list_holdings("fixture")] == ["fixture"]


def test_receipt_rejects_added_identity() -> None:
    snapshot = confirmation("fixture")
    (step,) = snapshot.plan.steps
    extra = step.resources[0].model_copy(update={"identity": "outside"})
    receipt = Receipt(
        operation_id=snapshot.operation_id,
        executor=step.executor,
        step_key=step.step_key,
        result=Result.observed(
            outcome=Outcome.CHANGED, message="Extra resource", resources=[extra]
        ),
    )
    with pytest.raises(ValueError, match="exceeds"):
        receipt.validate_for(snapshot.operation_id, step)


@pytest.mark.asyncio
async def test_cancellation_follows_verified_effects(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ledger = LedgerService(UnitOfWork(sessions))
    snapshot = confirmation("unchanged")
    (step,) = snapshot.plan.steps
    row = step.resources[0].model_copy(
        update={"before": "new", "ownership": ResourceOwnership.PREEXISTING}
    )
    step = step.model_copy(update={"resources": (row,)})
    snapshot = snapshot.model_copy(
        update={"plan": Plan(summary="Already present", steps=(step,), resources=[row])}
    )
    key = snapshot.operation_id
    await ledger.prepare(snapshot)
    await ledger.mark_running(key, step)
    result = Result.observed(
        outcome=Outcome.NO_CHANGES, message="No mutation", resources=list(step.resources)
    )
    await ledger.record_verified(key, step, result)
    assert (await ledger.interruption(key)) is Outcome.CANCELLED
    await ledger.finish(snapshot, Outcome.CANCELLED)
