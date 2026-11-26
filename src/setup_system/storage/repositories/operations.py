"""Confirm plans, reserve resources and record independent native readbacks."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import os
import socket
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from setup_system.execution.user_storage import private_directory
from setup_system.storage.models import (
    ActiveOperation,
    Change,
    Dependency,
    JournalEvent,
    Operation,
    PlanWarning,
    PreviousHolding,
    Release,
    Reservation,
    ResourceKey,
    Restore,
)
from setup_system.storage.repositories.history import (
    HistoryEntry,
    JournalEntry,
    StoredSnapshot,
    confirmed_snapshot,
    journal_entries,
    operation_history,
    record_events,
)
from setup_system.storage.repositories.recovery import confirm_restore, prepare_restore
from setup_system.storage.repositories.resources import (
    confirm_readback,
    holding_rows,
    observations,
    reserve_identity,
    resource_key,
    save_observation,
)
from setup_system.storage.session import read_session, write_session
from setup_system_sdk.enums.lifecycle import HostAction, LifecycleAction
from setup_system_sdk.enums.operation import (
    ActionMode,
    EventLevel,
    EventPhase,
    OperationState,
    Outcome,
)
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.schemas.common import ActionId, Identifier, Values
from setup_system_sdk.schemas.manifest import Manifest
from setup_system_sdk.schemas.operation import Plan, Resource, Result


async def _record(db: AsyncSession, key: UUID) -> tuple[Operation, Release]:
    row = (
        await db.execute(
            select(Operation, Release).join(Release).where(Operation.operation_id == key)
        )
    ).one_or_none()
    if row is None:
        raise ValueError("Unknown operation")
    return row[0], row[1]


async def _active_release(db: AsyncSession, plugin: str, digest: str) -> Release:
    row = await db.scalar(
        select(Release).where(Release.plugin_id == plugin, Release.digest == digest, Release.active)
    )
    if row is None:
        raise ValueError("The confirmed extension release is no longer active")
    return row


def _release_cancelled_lock(task: asyncio.Task[int]) -> None:
    if not task.cancelled() and task.exception() is None:
        os.close(task.result())


async def _start(
    db: AsyncSession,
    key: UUID,
    release: Release,
    action: ActionId,
    revision: str,
    inputs: Values,
    settings: Values,
    plan: Plan,
) -> Operation:
    operation = Operation(
        operation_id=key,
        release_id=release.id,
        action_id=action,
        status=OperationState.RUNNING,
        plan_summary=plan.summary,
        plan_outcome=plan.outcome,
        settings_revision=revision,
        inputs=inputs,
        settings=settings,
    )
    db.add(operation)
    await db.flush()
    db.add_all(
        PlanWarning(operation=operation.seq, position=i, message=message)
        for i, message in enumerate(plan.warnings)
    )
    return operation


class OperationRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        engine = factory.kw.get("bind")
        if not isinstance(engine, AsyncEngine) or engine.url.database is None:
            raise ValueError("Operation records require a bound SQLite engine")
        self._path = Path(engine.url.database)
        self._store_id = hashlib.sha256(os.fsencode(self._path)).digest()

    @asynccontextmanager
    async def claim_extension(self, plugin_id: str) -> AsyncIterator[None]:
        """Coordinate publication of a generation pointer with its database registration."""
        identity = TypeAdapter(Identifier).validate_python(plugin_id)

        def acquire() -> int:
            with private_directory(self._path.parent / "extension-locks" / identity) as directory:
                try:
                    fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("Another process is changing this extension") from None
                return os.dup(directory)

        opening = asyncio.create_task(asyncio.to_thread(acquire))
        try:
            fd = await asyncio.shield(opening)
        except asyncio.CancelledError:
            opening.add_done_callback(_release_cancelled_lock)
            raise
        try:
            yield
        finally:
            os.close(fd)

    @asynccontextmanager
    async def claim_reconciliation(self, operation_id: UUID) -> AsyncIterator[None]:
        """Exclude live verification while another host is executing this operation.

        Linux releases the abstract socket when its process dies. SQL reservations
        remain until the host records readback or a known pre-mutation refusal.
        """
        address = b"\0setup-system-v10-" + self._store_id + operation_id.bytes
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as guard:
            try:
                guard.bind(address)
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
                raise ValueError("This operation is active in another process") from None
            yield

    async def holdings(self, plugin_id: str) -> tuple[Resource, ...]:
        async with read_session(self.factory) as db:
            rows = await holding_rows(db, plugin_id)
            observed = await observations(db, [row.observation for row in rows])
            return tuple(observed[row.observation] for row in rows)

    async def dependents(self, plugin_id: str) -> list[str]:
        async with read_session(self.factory) as db:
            return list(
                await db.scalars(
                    select(Dependency.consumer)
                    .where(Dependency.provider == plugin_id)
                    .distinct()
                    .order_by(Dependency.consumer)
                )
            )

    async def reserve(
        self,
        operation_id: UUID,
        manifest: Manifest,
        digest: str,
        action_id: ActionId,
        *,
        settings_revision: str,
        inputs: Values,
        settings: Values,
        plan: Plan,
        holdings: Sequence[Resource],
        allow_preexisting_removal: bool = False,
    ) -> None:
        if action_id in tuple(HostAction) or any(
            r.kind == "secret-reference" for r in plan.resources
        ):
            raise ValueError("Extension actions cannot reserve the host keyring namespace")
        if not any(entry.id == action_id for entry in manifest.actions):
            raise ValueError("The active manifest does not declare the selected action")
        async with write_session(self.factory) as db:
            release = await _active_release(db, manifest.id, digest)
            await self._reserve(
                db,
                operation_id,
                release,
                action_id,
                settings_revision,
                inputs,
                settings,
                plan,
                holdings,
                manifest.dependencies,
                allow_preexisting_removal,
            )

    async def record_inspection(
        self,
        operation_id: UUID,
        manifest: Manifest,
        digest: str,
        action_id: ActionId,
        settings_revision: str,
        inputs: Values,
        settings: Values,
        plan: Plan,
        result: Result,
    ) -> None:
        descriptor = next((action for action in manifest.actions if action.id == action_id), None)
        if descriptor is None or descriptor.mode is ActionMode.MUTATION:
            raise ValueError("Only declared inspections and manual commands may skip reservation")
        async with write_session(self.factory) as db:
            release = await _active_release(db, manifest.id, digest)
            op = await _start(
                db, operation_id, release, action_id, settings_revision, inputs, settings, plan
            )
            readback = {(r.kind, r.identity): r for r in result.resources if r.verified}
            for position, desired in enumerate(plan.resources):
                key = await resource_key(db, desired)
                proposed = await save_observation(db, key.id, desired)
                observed = readback.get((desired.kind, desired.identity))
                db.add(
                    Change(
                        operation=op.seq,
                        resource=key.id,
                        position=position,
                        proposed=proposed,
                        native=(await save_observation(db, key.id, observed) if observed else None),
                    )
                )
            op.outcome = result.outcome
            op.status = {
                Outcome.CANCELLED: OperationState.CANCELLED,
                Outcome.FAILED: OperationState.FAILED,
                Outcome.BLOCKED: OperationState.FAILED,
                Outcome.UNKNOWN_OUTCOME: OperationState.UNKNOWN,
                Outcome.PARTIAL: OperationState.UNKNOWN,
            }.get(result.outcome, OperationState.SUCCEEDED)
            record_events(db, op, result)

    async def reserve_secret(
        self, plugin_id: str, digest: str, operation_id: UUID, reference: UUID, *, remove: bool
    ) -> Resource:
        async with write_session(self.factory) as db:
            release = await _active_release(db, plugin_id, digest)
            identity = str(reference)
            desired = Resource(
                kind="secret-reference",
                identity=identity,
                ownership=ResourceOwnership.CREATED,
                effect=ResourceEffect.REMOVE if remove else ResourceEffect.ENSURE,
                cleanup=True,
                before=identity if remove else None,
                after=None if remove else identity,
            )
            await self._reserve(
                db,
                operation_id,
                release,
                HostAction.REMOVE_SECRET if remove else HostAction.SAVE_SECRET,
                "",
                {},
                {},
                Plan(summary="Change the selected keyring reference", resources=[desired]),
                None,
                (),
                False,
            )
            return desired

    async def _reserve(
        self,
        db: AsyncSession,
        key: UUID,
        release: Release,
        action: ActionId,
        revision: str,
        inputs: Values,
        settings: Values,
        plan: Plan,
        holdings: Sequence[Resource] | None,
        dependencies: Sequence[str],
        allow_preexisting_removal: bool,
    ) -> None:
        if plan.outcome not in (Outcome.READY, Outcome.NO_CHANGES):
            raise ValueError("The confirmed plan is not ready for execution")
        operation = await _start(db, key, release, action, revision, inputs, settings, plan)
        seq = operation.seq
        db.add(ActiveOperation(plugin_id=release.plugin_id, operation=seq))
        db.add(
            JournalEvent(
                operation=seq,
                status=OperationState.RUNNING,
                level=EventLevel.INFO,
                phase=EventPhase.PREPARED,
                message_code="operation_reserved",
                resource_count=len(plan.resources),
            )
        )
        previous = await holding_rows(db, release.plugin_id)
        if holdings is not None:
            before = await observations(db, [row.observation for row in previous])
            if tuple(holdings) != tuple(before[row.observation] for row in previous):
                raise ValueError("Ownership changed since preview")
        db.add_all(
            PreviousHolding(operation=seq, resource=row.resource, observation=row.observation)
            for row in previous
        )
        if action == LifecycleAction.UNINSTALL:
            if (
                await db.scalar(
                    select(Dependency.consumer)
                    .where(Dependency.provider == release.plugin_id)
                    .limit(1)
                )
                is not None
            ):
                raise ValueError("Another installed or pending extension requires this program")
        else:
            for provider in dependencies:
                removing = (
                    select(ActiveOperation)
                    .join(Operation, Operation.seq == ActiveOperation.operation)
                    .where(
                        ActiveOperation.plugin_id == provider,
                        Operation.action_id == LifecycleAction.UNINSTALL,
                    )
                )
                if await db.scalar(removing) is not None:
                    raise ValueError("A required program has a removal in progress")
                db.add(Dependency(consumer=release.plugin_id, provider=provider, operation=seq))
        for index, desired in enumerate(plan.resources):
            rid = await reserve_identity(
                db, release.plugin_id, desired, allow_preexisting_removal=allow_preexisting_removal
            )
            oid = await save_observation(db, rid, desired)
            db.add(Change(resource=rid, operation=seq, proposed=oid, position=index))
            await db.flush()
            db.add(Reservation(resource=rid, operation=seq))

    async def snapshot(self, operation_id: UUID) -> StoredSnapshot:
        async with read_session(self.factory) as db:
            op, release = await _record(db, operation_id)
            return await confirmed_snapshot(db, op, release)

    async def pending(self, plugin_id: str) -> list[UUID]:
        async with read_session(self.factory) as db:
            return list(
                await db.scalars(
                    select(Operation.operation_id)
                    .join(ActiveOperation, Operation.seq == ActiveOperation.operation)
                    .where(ActiveOperation.plugin_id == plugin_id)
                )
            )

    async def pending_readbacks(self, plugin_id: str) -> list[UUID]:
        """Only send extension commands to the extension that originally prepared them."""
        async with read_session(self.factory) as db:
            query = (
                select(Operation.operation_id)
                .join(ActiveOperation, Operation.seq == ActiveOperation.operation)
                .outerjoin(Restore, Restore.operation == Operation.seq)
                .where(
                    ActiveOperation.plugin_id == plugin_id,
                    Restore.operation.is_(None),
                    Operation.action_id.not_in(tuple(HostAction)),
                )
            )
            return list(await db.scalars(query))

    async def history(self, plugin_id: str | None = None) -> list[HistoryEntry]:
        async with read_session(self.factory) as db:
            return await operation_history(db, plugin_id)

    async def events(
        self, plugin: str | None = None, *, limit: int | None = None
    ) -> list[JournalEntry]:
        async with read_session(self.factory) as db:
            return await journal_entries(db, plugin, limit)

    async def record_readback(
        self, operation_id: UUID, observations: Sequence[Resource], receipt: str | None = None
    ) -> None:
        async with write_session(self.factory) as db:
            op, _ = await _record(db, operation_id)
            if op.status not in (OperationState.RUNNING, OperationState.UNKNOWN):
                raise ValueError("The operation is already final")
            if receipt is not None:
                if op.receipt is not None and op.receipt != receipt:
                    raise ValueError("A different restore receipt was already recorded")
                op.receipt = receipt
            db.add(
                JournalEvent(
                    operation=op.seq,
                    status=op.status,
                    level=EventLevel.INFO,
                    phase=EventPhase.APPLIED,
                    message_code="native_readback",
                    resource_count=sum(row.verified for row in observations),
                )
            )
            for row in observations:
                if not row.verified:
                    continue
                change = await db.scalar(
                    select(Change)
                    .join(ResourceKey)
                    .where(
                        Change.operation == op.seq,
                        ResourceKey.kind == row.kind,
                        ResourceKey.identity == row.identity,
                    )
                )
                if change is None or change.native is not None:
                    raise ValueError("Unexpected or duplicate native readback")
                change.native = await save_observation(db, change.resource, row)

    async def finish(self, operation_id: UUID, result: Result, *, recovered: bool = False) -> None:
        async with write_session(self.factory) as db:
            op, release = await _record(db, operation_id)
            if op.status not in (OperationState.RUNNING, OperationState.UNKNOWN):
                raise ValueError("The operation is already final")
            changes = list(await db.scalars(select(Change).where(Change.operation == op.seq)))
            evidence = await observations(
                db,
                [i for c in changes for i in (c.proposed, c.native, c.confirmed) if i is not None],
            )
            confirmed = {(r.kind, r.identity): r for r in result.resources}
            expected = {(evidence[c.proposed].kind, evidence[c.proposed].identity) for c in changes}
            if len(confirmed) != len(result.resources) or confirmed.keys() - expected:
                raise ValueError("Readback includes a duplicate or unapproved resource")
            success = result.outcome in (
                Outcome.COMPLETE,
                Outcome.CHANGED,
                Outcome.NO_CHANGES,
                Outcome.NEEDS_REBOOT,
            )
            if success and expected != confirmed.keys():
                raise ValueError("Readback is incomplete")
            for c in changes:
                desired = evidence[c.proposed]
                row = confirmed.get((desired.kind, desired.identity))
                if row is None or not row.verified:
                    if success:
                        raise ValueError("Resource verification is incomplete")
                    continue
                if c.confirmed is not None:
                    if row != evidence[c.confirmed]:
                        raise ValueError("A confirmed resource changed")
                    continue
                native = evidence.get(c.native) if c.native is not None else None
                if (
                    (recovered or op.status is OperationState.UNKNOWN)
                    and native is None
                    and desired.ownership is ResourceOwnership.CREATED
                    and desired.cleanup
                    and desired.effect is ResourceEffect.ENSURE
                ):
                    raise ValueError(
                        "A later observation cannot establish who created this resource"
                    )
                await confirm_readback(db, op, release.plugin_id, c, desired, native, row)
            if result.recovery_id:
                if op.receipt is not None and op.receipt != result.recovery_id:
                    raise ValueError("The restore receipt changed")
                op.receipt = result.recovery_id
            if success:
                op.status = OperationState.SUCCEEDED
                await db.execute(
                    update(Dependency).where(Dependency.operation == op.seq).values(confirmed=True)
                )
                if op.action_id == LifecycleAction.UNINSTALL:
                    await db.execute(
                        delete(Dependency).where(Dependency.consumer == release.plugin_id)
                    )
            elif (
                result.outcome in (Outcome.UNKNOWN_OUTCOME, Outcome.PARTIAL)
                or op.receipt
                or any(c.native is not None or c.confirmed is not None for c in changes)
            ):
                op.status = OperationState.UNKNOWN
            elif op.status is OperationState.UNKNOWN:
                raise ValueError("An unknown outcome requires conclusive readback")
            else:
                op.status = (
                    OperationState.CANCELLED
                    if result.outcome is Outcome.CANCELLED
                    else OperationState.FAILED
                )
            op.outcome = result.outcome
            record_events(db, op, result)
            if op.status is not OperationState.UNKNOWN:
                await db.execute(delete(Reservation).where(Reservation.operation == op.seq))
                await db.execute(delete(ActiveOperation).where(ActiveOperation.operation == op.seq))
                await db.execute(
                    delete(Dependency).where(
                        Dependency.operation == op.seq, Dependency.confirmed.is_(False)
                    )
                )

    async def recoveries(self, plugin_id: str) -> list[tuple[UUID, str]]:
        async with read_session(self.factory) as db:
            query = (
                select(Operation.operation_id, Operation.receipt)
                .join(Release)
                .outerjoin(Restore)
                .where(
                    Release.plugin_id == plugin_id,
                    Operation.receipt.is_not(None),
                    (Restore.operation.is_(None)) | (Restore.complete.is_(False)),
                )
                .order_by(Operation.seq.desc())
            )
            return [
                (key, receipt) for key, receipt in await db.execute(query) if receipt is not None
            ]

    async def reserve_restore(self, operation_id: UUID, receipt: str, identities: set[str]) -> None:
        async with write_session(self.factory) as db:
            op, release = await _record(db, operation_id)
            await prepare_restore(db, op, release, receipt, identities)

    async def finish_restore(self, operation_id: UUID) -> None:
        async with write_session(self.factory) as db:
            op, release = await _record(db, operation_id)
            await confirm_restore(db, op, release)
