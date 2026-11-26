"""Reserve verified restore targets and reinstate previous ownership."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from setup_system.storage.models import (
    ActiveOperation,
    Change,
    Holding,
    JournalEvent,
    Operation,
    PreviousHolding,
    Release,
    Reservation,
    ResourceKey,
    Restore,
)
from setup_system_sdk.enums.operation import (
    EventLevel,
    EventPhase,
    OperationState,
    Outcome,
)


async def prepare_restore(
    db: AsyncSession, op: Operation, release: Release, receipt: str, identities: set[str]
) -> None:
    if not identities or op.receipt != receipt or op.status is not OperationState.SUCCEEDED:
        raise ValueError("No verified operation owns these restore targets")
    if await db.get(Restore, op.seq):
        raise ValueError("Restore has already started. Inspect its outcome before retrying.")
    rows = list(
        await db.execute(
            select(Change, ResourceKey)
            .join(ResourceKey)
            .where(Change.operation == op.seq, ResourceKey.identity.in_(identities))
        )
    )
    if {r.identity for _, r in rows} != identities:
        raise ValueError("The receipt refers to resources outside the operation")
    db.add(ActiveOperation(plugin_id=release.plugin_id, operation=op.seq))
    db.add(Restore(operation=op.seq))
    db.add(
        JournalEvent(
            operation=op.seq,
            status=OperationState.RUNNING,
            level=EventLevel.INFO,
            phase=EventPhase.PREPARED,
            message_code="restore_reserved",
            resource_count=len(rows),
        )
    )
    for change, key in rows:
        other = await db.scalar(
            select(Holding)
            .where(Holding.resource == key.id, Holding.plugin_id != release.plugin_id)
            .limit(1)
        )
        newer = await db.scalar(
            select(Change)
            .outerjoin(Restore, Restore.operation == Change.operation)
            .where(
                Change.resource == key.id,
                Change.operation > op.seq,
                Change.confirmed.is_not(None),
                Restore.complete.is_not(True),
            )
            .limit(1)
        )
        if other or newer or change.confirmed is None:
            raise ValueError("Later ownership or incomplete verification prevents restore")
        db.add(Reservation(resource=key.id, operation=op.seq))


async def confirm_restore(db: AsyncSession, op: Operation, release: Release) -> None:
    restore = await db.get(Restore, op.seq)
    if restore is None or restore.complete:
        raise ValueError("Restore has no active reservation")
    for rid in await db.scalars(
        select(Reservation.resource).where(Reservation.operation == op.seq)
    ):
        previous = await db.get(PreviousHolding, (op.seq, rid))
        await db.execute(
            delete(Holding).where(Holding.resource == rid, Holding.plugin_id == release.plugin_id)
        )
        if previous:
            db.add(
                Holding(
                    plugin_id=release.plugin_id,
                    resource=rid,
                    observation=previous.observation,
                )
            )
    restore.complete = True
    db.add(
        JournalEvent(
            operation=op.seq,
            status=OperationState.SUCCEEDED,
            level=EventLevel.INFO,
            phase=EventPhase.VERIFIED,
            message_code="restore_verified",
            outcome=Outcome.COMPLETE,
        )
    )
    await db.execute(delete(Reservation).where(Reservation.operation == op.seq))
    await db.execute(delete(ActiveOperation).where(ActiveOperation.operation == op.seq))
