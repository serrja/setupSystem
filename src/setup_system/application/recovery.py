"""Inspect retained evidence before offering a separately confirmed recovery action."""

from contextlib import ExitStack
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

import anyio
from pydantic import ValidationError
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome, WorkerMethod
from setup_system_sdk.enums.resources import ResourceEffect
from setup_system_sdk.manifests import Requirement
from setup_system_sdk.operations import Executor, Result
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs, Values
from setup_system_sdk.schemas.wire import Request

from setup_system.application.authorization import Authorize
from setup_system.application.ledger import ResumePlan
from setup_system.application.operations import OperationService
from setup_system.domain.confirmation import ConfirmedSnapshot as Snapshot
from setup_system.domain.history import ArchivedResources, decode_historical
from setup_system.domain.operations import (
    SUCCESS,
    ChangePhase,
    OperationPhase,
    RollbackPhase,
    StepEvidence,
    StepPhase,
)
from setup_system.domain.resources import holding, validate_observation
from setup_system.errors import OperationError
from setup_system.infrastructure.database.models import Operation
from setup_system.infrastructure.database.uow import Repositories, UnitOfWork
from setup_system.infrastructure.locks import acquire
from setup_system.infrastructure.plugins.registry import PluginRegistry


class OperationHistoryItem(Inputs):
    operation_id: str
    plugin_id: Identifier
    action_id: ActionId
    phase: OperationPhase
    outcome: Outcome | None
    started: datetime | None

    @classmethod
    def from_record(cls, row: Operation) -> "OperationHistoryItem":
        return cls(
            operation_id=row.id,
            plugin_id=row.plugin,
            action_id=row.action,
            phase=row.phase,
            outcome=row.outcome,
            started=datetime.fromisoformat(row.started) if row.started else None,
        )


class RecoveryAction(StrEnum):
    ROLLBACK = "rollback"
    RESUME = "resume"
    RECONCILE = "reconcile"


class ReconciliationPlan(Inputs):
    operation_id: UUID
    evidence_digest: str | None = None
    executor: Executor | None = None
    result: Result


class _HistoricalEvidence(Inputs):
    operation: UUID
    owner: str
    action: str
    release_digest: str
    receipt: str | None
    snapshot: Values
    approved: ArchivedResources
    native: tuple[Resource, ...]
    published: tuple[tuple[str, str], ...]

    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


async def _historical(repos: Repositories, operation: UUID) -> _HistoricalEvidence:
    key = str(operation)
    op = await repos.operations.get(key)
    if op.phase is OperationPhase.VERIFIED:
        raise ValueError("This operation was already finalized. Inspect its history.")
    if await repos.operations.rollback_phase(key) is not None:
        raise ValueError(
            "Inspect the original restore journal before reconciling this historical restore."
        )
    document = await repos.operations.confirmation(key)
    if not document:
        raise ValueError("Restore the missing original confirmation from the pre-migration backup")
    snapshot, archive = decode_historical(
        document.snapshot,
        document.historical_resources,
        operation,
        op.plugin,
        op.release,
        op.action,
    )
    changes = await repos.operations.changes(key)
    approved = {(r.kind, r.identity): r for r in archive.resources}
    if (
        not approved
        or len(approved) != len(archive.resources)
        or set(approved) != {(c.kind, c.identity) for c in changes}
    ):
        raise ValueError(
            "Compare the original resource approval with the preserved database backup"
        )
    published = tuple((c.kind, c.identity) for c in changes if c.phase is ChangePhase.VERIFIED)
    native = sorted(await repos.operations.evidence(key), key=lambda r: (r.kind, r.identity))
    if {(r.kind, r.identity) for r in native} != set(approved) or not all(
        r.verified for r in native
    ):
        raise ValueError(
            "Restore the native execution evidence for each resource from its original journal."
        )
    for row in native:
        validate_observation(approved[(row.kind, row.identity)], row, row)
    return _HistoricalEvidence(
        operation=operation,
        owner=op.plugin,
        action=op.action,
        release_digest=op.release,
        snapshot=snapshot,
        receipt=document.recovery,
        approved=archive,
        native=tuple(native),
        published=published,
    )


def _validate_historical_readback(source: _HistoricalEvidence, result: Result) -> None:
    if result.outcome is not Outcome.COMPLETE:
        raise ValueError("Historical reconciliation requires complete readback")
    approved = {(r.kind, r.identity): r for r in source.approved.resources}
    native = {(r.kind, r.identity): r for r in source.native}
    if len(result.resources) != len(approved) or {
        (r.kind, r.identity) for r in result.resources
    } != set(approved):
        raise ValueError("The recovery provider did not verify every original resource")
    for row in result.resources:
        key = row.kind, row.identity
        if not row.verified:
            raise ValueError("The recovery provider returned unverified evidence")
        validate_observation(approved[key], native[key], row)


async def _rollback_candidate(repos: Repositories, op: Operation, snapshot: Snapshot) -> bool:
    if (
        op.outcome not in SUCCESS
        or snapshot.rollback_of
        or not snapshot.plan.steps
        or await repos.operations.rollback_phase(op.id) is not None
    ):
        return False
    steps = await repos.operations.steps(op.id)
    if len(steps) != len(snapshot.plan.steps):
        return False
    for state, step in zip(steps, snapshot.plan.steps, strict=True):
        if (
            state.phase is not StepPhase.VERIFIED
            or step.capability
            or step.executor != snapshot.executor
            or state.step_key != step.step_key
        ):
            return False
        applied = StepEvidence.decode(state.receipt).applied
        if applied is None or not applied.result.recovery_id:
            return False
    return True


async def retained_for_recovery(uow: UnitOfWork, plugin: str, registry: PluginRegistry) -> bool:
    candidates: dict[Executor, set[str]] = {}
    async with uow.read() as repos:
        for row in await repos.operations.history():
            unfinished = row.phase is not OperationPhase.VERIFIED
            if unfinished and row.plugin == plugin:
                return True
            confirmation = await repos.operations.confirmation(row.id)
            try:
                snapshot = (
                    Snapshot.model_validate_json(confirmation.snapshot) if confirmation else None
                )
            except ValidationError:
                snapshot = None
            if snapshot:
                if plugin not in {e.plugin_id for e in snapshot.executors()}:
                    continue
                if unfinished:
                    return True
            marker = await repos.operations.rollback_phase(row.id)
            if marker is RollbackPhase.VERIFIED:
                continue
            if row.plugin == plugin and marker in (
                RollbackPhase.PREPARED,
                RollbackPhase.UNRESOLVED,
            ):
                return True
            if row.outcome not in SUCCESS:
                continue
            if snapshot is None and row.plugin == plugin and confirmation and confirmation.recovery:
                # Historical journals can hold a file receipt without revision-three steps.
                return True
            for state in await repos.operations.steps(row.id):
                if state.phase is not StepPhase.VERIFIED:
                    continue
                try:
                    receipt = StepEvidence.decode(state.receipt).applied
                except ValidationError:
                    if row.plugin == plugin or snapshot:
                        return True
                    continue
                if (
                    not receipt
                    or not receipt.result.recovery_id
                    or plugin not in (row.plugin, receipt.executor.plugin_id)
                ):
                    continue
                if (
                    not snapshot
                    or str(receipt.operation_id) != row.id
                    or receipt.step_key != state.step_key
                ):
                    return True
                candidates.setdefault(snapshot.executor, set()).add(snapshot.action_id)
    for identity, actions in candidates.items():
        try:
            release = await anyio.to_thread.run_sync(registry.resolve_executor, identity)
        except (OSError, ValueError):
            # Preserve the remaining files while the owner repairs the execution environment.
            return True
        if any(
            action.id in actions and action.supports_rollback for action in release.manifest.actions
        ):
            return True
    return False


class RecoveryService:
    def __init__(self, uow: UnitOfWork, operations: OperationService) -> None:
        self.uow = uow
        self.operations = operations

    async def history(
        self, plugin: str | None = None, *, unfinished: bool = False
    ) -> list[OperationHistoryItem]:
        async with self.uow.read() as repos:
            return [
                OperationHistoryItem.from_record(r)
                for r in await repos.operations.history(plugin, unfinished=unfinished)
            ]

    async def available(self, action: RecoveryAction) -> list[OperationHistoryItem]:
        candidates = []
        async with self.uow.read() as repos:
            for row in await repos.operations.history(
                unfinished=action is not RecoveryAction.ROLLBACK
            ):
                if row.id in {str(key) for key in self.operations.pending}:
                    continue
                doc = await repos.operations.confirmation(row.id)
                try:
                    snapshot = Snapshot.model_validate_json(doc.snapshot) if doc else None
                except ValidationError:
                    snapshot = None
                if snapshot is None:
                    if action is RecoveryAction.RECONCILE:
                        candidates.append(OperationHistoryItem.from_record(row))
                    continue
                if action is RecoveryAction.RESUME or (
                    action is RecoveryAction.ROLLBACK
                    and row.phase is OperationPhase.VERIFIED
                    and await _rollback_candidate(repos, row, snapshot)
                ):
                    candidates.append(OperationHistoryItem.from_record(row))
        if action is RecoveryAction.ROLLBACK:
            available = []
            for item in candidates:
                try:
                    await self.rollback(UUID(item.operation_id))
                except (ValueError, RuntimeError, OSError):
                    continue
                available.append(item)
            return available
        return candidates

    async def inspect(self, operation: UUID) -> Result:
        key = str(operation)
        async with self.uow.read() as repos:
            document = await repos.operations.confirmation(key)
            try:
                snapshot = Snapshot.model_validate_json(document.snapshot) if document else None
            except ValidationError:
                snapshot = None
        if snapshot is None:
            return (await self.preview_reconciliation(operation)).result
        async with acquire(
            self.operations.locks,
            ["operation/" + key, "plugin/" + snapshot.executor.plugin_id],
            wait=False,
        ):
            with ExitStack() as leases:
                try:
                    leases.enter_context(self.operations.registry.leases(snapshot.executors()))
                except ValueError as exc:
                    return Result(outcome=Outcome.UNKNOWN_OUTCOME, message=str(exc))
                async with self.uow.read() as repos:
                    rows = await repos.operations.steps(key)
                if [r.step_key for r in rows] != [s.step_key for s in snapshot.plan.steps]:
                    return Result(
                        outcome=Outcome.UNKNOWN_OUTCOME,
                        message="Recover the original execution steps before resuming.",
                    )
                for state, step in zip(rows, snapshot.plan.steps, strict=True):
                    if state.phase in (StepPhase.PREPARED, StepPhase.VERIFIED):
                        continue
                    receipt = StepEvidence.decode(state.receipt).applied
                    async with acquire(
                        self.operations.locks, ["step/" + k for k in step.lock_keys], wait=True
                    ):
                        observed = await self.operations.readback(snapshot, step, receipt)
                        try:
                            await self.operations.ledger.record_verified(operation, step, observed)
                        except ValueError as exc:
                            observed = Result(outcome=Outcome.UNKNOWN_OUTCOME, message=str(exc))
                            await self.operations.ledger.record_verified(operation, step, observed)
                        if observed.outcome not in SUCCESS:
                            return observed
        return Result(
            outcome=Outcome.NEEDS_USER_ACTION,
            message="Readback recorded. Review the recovery plan before changing any resources.",
        )

    async def preview_reconciliation(self, operation: UUID) -> ReconciliationPlan:
        try:
            async with self.uow.read() as repos:
                source = await _historical(repos, operation)
            return await self._observe(source)
        except OperationError as exc:
            return ReconciliationPlan(
                operation_id=operation,
                result=Result(outcome=Outcome.UNKNOWN_OUTCOME, message=exc.detail.message),
            )
        except (ValueError, OSError, RuntimeError) as exc:
            reason = (
                str(exc)
                if type(exc) is ValueError
                else "Restore the approved recovery provider and its execution record."
            )
            return ReconciliationPlan(
                operation_id=operation,
                result=Result(outcome=Outcome.UNKNOWN_OUTCOME, message=reason),
            )

    async def _observe(self, source: _HistoricalEvidence) -> ReconciliationPlan:
        cap = "operation.recovery"
        provider = self.operations.registry.provider(Requirement(id=cap, version=">=1,<2"))
        with self.operations.registry.leases({provider.executor}):
            await anyio.to_thread.run_sync(
                self.operations.launcher.trust.resolve_handler, provider.executor, cap
            )
            query = CapabilityRequest(
                capability=cap,
                step_key="recovery",
                payload={
                    "owner": source.owner,
                    "release_digest": source.release_digest,
                    "operation": str(source.operation),
                    "snapshot": {
                        name: value
                        for name, value in source.snapshot.items()
                        if name
                        in {
                            "revision",
                            "release",
                            "digest",
                            "plugin_id",
                            "operation_id",
                            "version",
                            "source_operation",
                        }
                    },
                    "receipt": source.receipt,
                    "approved": [r.model_dump(mode="json") for r in source.approved.resources],
                    "native": [r.model_dump(mode="json") for r in source.native],
                },
            )
            reply = await self.operations.workers.call(
                provider,
                Request(
                    plugin_id=provider.manifest.id,
                    operation_id=source.operation,
                    method=WorkerMethod.PROVIDER_INSPECT,
                    capability_request=query,
                ),
            )
        result = Result.model_validate((reply.facts or {}).get("result"))
        if result.outcome is Outcome.COMPLETE:
            _validate_historical_readback(source, result)
        return ReconciliationPlan(
            operation_id=source.operation,
            evidence_digest=source.digest(),
            executor=provider.executor,
            result=result,
        )

    async def reconcile(self, plan: ReconciliationPlan) -> Result:
        if plan.result.outcome is not Outcome.COMPLETE or not plan.executor:
            raise ValueError("Conclusive provider readback is required to reconcile the ledger")
        key = str(plan.operation_id)
        async with acquire(self.operations.locks, ["operation/" + key], wait=False):
            if await self.preview_reconciliation(plan.operation_id) != plan:
                raise ValueError(
                    "The historical observation changed. Inspect it again before confirming."
                )
            await self._finalize_reconciliation(plan)
        return plan.result

    async def _finalize_reconciliation(self, plan: ReconciliationPlan) -> None:
        key = str(plan.operation_id)
        async with self.uow.transaction() as repos:
            source = await _historical(repos, plan.operation_id)
            if source.digest() != plan.evidence_digest:
                raise ValueError("The stored historical evidence changed")
            _validate_historical_readback(source, plan.result)
            reserved = await repos.resources.reservations(plan.result.resources)
            owners = await repos.resources.owners(plan.result.resources)
            previous = {(r.kind, r.identity): r for r in source.approved.holdings}
            fresh = []
            for row in plan.result.resources:
                identity = row.kind, row.identity
                if identity in source.published:
                    expected = (
                        holding(row, previous.get(identity))
                        if row.effect is ResourceEffect.ENSURE
                        else None
                    )
                    if owners.get(identity, {}).get(source.owner) != expected or reserved.get(
                        identity
                    ) not in (None, key):
                        raise ValueError(
                            "A later operation changed this published holding. Review its history."
                        )
                    continue
                if reserved.get(identity) != key or owners.get(identity, {}).get(
                    source.owner
                ) != previous.get(identity):
                    raise ValueError(
                        "Inspect newer operations. A reservation or earlier holding changed."
                    )
                fresh.append(row)
            await repos.resources.save_holdings(
                source.owner,
                [
                    holding(r, previous.get((r.kind, r.identity)))
                    for r in fresh
                    if r.effect is ResourceEffect.ENSURE
                ],
            )
            await repos.resources.remove_holdings(
                source.owner, [r for r in fresh if r.effect is ResourceEffect.REMOVE]
            )
            await repos.operations.change_phase(key, fresh, ChangePhase.VERIFIED)
            await repos.operations.save_reconciliation(key, plan.model_dump_json())
            await repos.dependencies.finish(
                key,
                source.owner,
                confirm=True,
                uninstall=source.action == LifecycleAction.UNINSTALL,
            )
            await repos.resources.release(key)
            await repos.operations.set_outcome(key, OperationPhase.VERIFIED, plan.result.outcome)

    async def rollback(self, operation: UUID) -> Snapshot:
        return await self.operations.rollback_plan(operation)

    async def plan(self, operation: UUID) -> ResumePlan:
        proposal = await self.operations.ledger.resume_plan(operation)
        with self.operations.registry.leases(proposal.snapshot.executors()):
            return proposal

    async def apply(self, plan: ResumePlan, authorize: Authorize | None = None) -> Result:
        return await self.operations.apply(plan.snapshot, authorize, resume=plan)
