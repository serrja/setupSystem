"""Read immutable confirmations and emit host-bound journal events."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from setup_system.storage.models import (
    Change,
    JournalEvent,
    Operation,
    PlanWarning,
    PreviousHolding,
    Release,
    ResourceKey,
)
from setup_system.storage.repositories.resources import (
    observations,
)
from setup_system_sdk.enums.operation import (
    EventLevel,
    EventPhase,
    OperationState,
    Outcome,
)
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs, Values
from setup_system_sdk.schemas.operation import Plan, Resource, Result


class StoredSnapshot(Inputs):
    operation_id: UUID
    digest: str
    plugin_id: Identifier
    action_id: ActionId
    settings_revision: str
    settings: Values
    inputs: Values
    holdings: tuple[Resource, ...]
    plan: Plan


class HistoryEntry(Inputs):
    operation_id: UUID
    plugin_id: Identifier
    action_id: ActionId
    started_at: datetime
    status: OperationState
    outcome: Outcome | None


class JournalEntry(Inputs):
    timestamp: datetime
    operation_id: UUID
    plugin_id: Identifier
    action_id: ActionId
    status: OperationState
    outcome: Outcome | None
    level: EventLevel
    phase: EventPhase
    message_code: Identifier
    resource_count: int | None


async def confirmed_snapshot(db: AsyncSession, op: Operation, release: Release) -> StoredSnapshot:

    approved = list(
        await db.scalars(
            select(Change.proposed).where(Change.operation == op.seq).order_by(Change.position)
        )
    )
    previous = list(
        await db.scalars(
            select(PreviousHolding.observation)
            .join(ResourceKey)
            .where(PreviousHolding.operation == op.seq)
            .order_by(ResourceKey.kind, ResourceKey.identity)
        )
    )
    warnings = list(
        await db.scalars(
            select(PlanWarning.message)
            .where(PlanWarning.operation == op.seq)
            .order_by(PlanWarning.position)
        )
    )
    evidence = await observations(db, approved + previous)
    return StoredSnapshot(
        operation_id=op.operation_id,
        digest=release.digest,
        plugin_id=release.plugin_id,
        action_id=op.action_id,
        settings_revision=op.settings_revision,
        inputs=op.inputs,
        settings=op.settings,
        holdings=tuple(evidence[i] for i in previous),
        plan=Plan(
            summary=op.plan_summary,
            outcome=op.plan_outcome,
            warnings=warnings,
            resources=[evidence[i] for i in approved],
        ),
    )


async def operation_history(db: AsyncSession, plugin_id: str | None) -> list[HistoryEntry]:

    query = select(Operation, Release).join(Release).order_by(Operation.seq.desc()).limit(200)
    if plugin_id is not None:
        query = query.where(Release.plugin_id == plugin_id)
    return [
        HistoryEntry(
            operation_id=op.operation_id,
            plugin_id=release.plugin_id,
            action_id=op.action_id,
            status=op.status,
            outcome=op.outcome,
            started_at=op.started_at,
        )
        for op, release in await db.execute(query)
    ]


async def journal_entries(
    db: AsyncSession, plugin: str | None, limit: int | None
) -> list[JournalEntry]:

    query = (
        select(JournalEvent, Operation, Release)
        .join(Operation, Operation.seq == JournalEvent.operation)
        .join(Release, Release.id == Operation.release_id)
        .order_by(JournalEvent.id)
    )
    if plugin is not None:
        query = query.where(Release.plugin_id == plugin)
    if limit is not None:
        if limit < 1:
            raise ValueError("The event limit must be positive")
        query = query.order_by(None).order_by(JournalEvent.id.desc()).limit(limit)
    entries = [
        JournalEntry(
            timestamp=event.timestamp,
            operation_id=op.operation_id,
            action_id=op.action_id,
            plugin_id=release.plugin_id,
            status=event.status,
            outcome=event.outcome,
            level=event.level,
            phase=event.phase,
            message_code=event.message_code,
            resource_count=event.resource_count,
        )
        for event, op, release in await db.execute(query)
    ]
    if limit is not None:
        entries.reverse()
    return entries


def record_events(db: AsyncSession, op: Operation, result: Result) -> None:
    db.add_all(
        JournalEvent(
            operation=op.seq,
            status=op.status,
            outcome=event.outcome,
            level=event.level,
            phase=event.phase,
            message_code="plugin_observation",
            resource_count=event.details.get("resource_count"),
        )
        for event in result.events
    )
    phase = {
        OperationState.SUCCEEDED: EventPhase.VERIFIED,
        OperationState.UNKNOWN: EventPhase.UNRESOLVED,
        OperationState.CANCELLED: EventPhase.BLOCKED,
        OperationState.FAILED: EventPhase.BLOCKED,
    }[op.status]
    db.add(
        JournalEvent(
            operation=op.seq,
            status=op.status,
            outcome=result.outcome,
            level=EventLevel.INFO if op.status is OperationState.SUCCEEDED else EventLevel.WARNING,
            phase=phase,
            message_code="operation_finished",
            resource_count=len(result.resources),
        )
    )
