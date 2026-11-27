"""Prepare a reverse file plan from the original executor and its retained receipts."""

from uuid import UUID, uuid4

import anyio
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.operations import Plan
from setup_system_sdk.schemas.wire import Request

from setup_system.application.ledger import LedgerService
from setup_system.domain.confirmation import ConfirmedSnapshot as Snapshot
from setup_system.domain.operations import SUCCESS, OperationPhase, StepEvidence
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient


class RollbackPlanner:
    def __init__(
        self, ledger: LedgerService, registry: PluginRegistry, workers: WorkerClient
    ) -> None:
        self.ledger = ledger
        self.registry = registry
        self.workers = workers

    async def plan(self, original: UUID, *, operation_id: UUID | None = None) -> Snapshot:
        async with self.ledger.uow.read() as repos:
            source = Snapshot.model_validate_json(await repos.operations.snapshot(str(original)))
            operation = await repos.operations.get(str(original))
            if (
                source.rollback_of
                or operation.phase is not OperationPhase.VERIFIED
                or operation.outcome not in SUCCESS
            ):
                raise ValueError("Select a completed original operation")
            evidence = {
                row.step_key: row.receipt for row in await repos.operations.steps(str(original))
            }
            holdings = tuple(await repos.resources.list_holdings(source.executor.plugin_id))
        release = await anyio.to_thread.run_sync(self.registry.resolve_executor, source.executor)
        steps = []
        with self.registry.leases({source.executor}):
            for step in reversed(source.plan.steps):
                raw = evidence.get(step.step_key)
                if not raw or step.executor != source.executor or step.capability:
                    raise ValueError("This operation requires its owner's recovery procedure")
                receipt = StepEvidence.decode(raw).applied
                if not receipt:
                    raise ValueError("The original file publication has no receipt")
                receipt.validate_for(original, step)
                reply = await self.workers.call(
                    release,
                    Request(
                        plugin_id=source.executor.plugin_id,
                        operation_id=original,
                        method=WorkerMethod.RESTORE_PLAN,
                        snapshot=source.worker_snapshot(),
                        step_key=step.step_key,
                        receipt=receipt,
                    ),
                )
                restore = reply.step
                if (
                    restore is None
                    or restore.executor != source.executor
                    or restore.capability
                    or restore.handler != "restore-files"
                    or restore.step_key != step.step_key
                    or restore.permissions != {Permission.USER_FILES}
                    or {(r.kind, r.identity, r.after, r.before) for r in restore.resources}
                    != {(r.kind, r.identity, r.before, r.after) for r in step.resources}
                ):
                    raise ValueError("Rollback differs from the approved publication")
                steps.append(restore)
        if not steps:
            raise ValueError("The operation has no file rollback steps")
        return Snapshot(
            operation_id=operation_id or uuid4(),
            rollback_of=original,
            executor=source.executor,
            action_id="restore-files",
            settings_revision=source.settings_revision,
            settings={},
            inputs={},
            holdings=holdings,
            plan=Plan(
                summary="Restore original managed files",
                steps=tuple(steps),
                resources=[r for step in steps for r in step.resources],
            ),
        )
