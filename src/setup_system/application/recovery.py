"""Inspect retained evidence before offering a separately confirmed recovery action."""

from uuid import UUID

import anyio
from pydantic import TypeAdapter, ValidationError
from setup_system_sdk.enums.operation import Outcome, WorkerMethod
from setup_system_sdk.manifests import Requirement
from setup_system_sdk.operations import Result, Snapshot, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.wire import Request

from setup_system.application.authorization import Authorize
from setup_system.application.ledger import ResumePlan
from setup_system.application.operations import OperationService
from setup_system.domain.operations import SUCCESS, StepEvidence, StepPhase
from setup_system.infrastructure.database.uow import UnitOfWork
from setup_system.infrastructure.locks import acquire


class RecoveryService:
    def __init__(self, uow: UnitOfWork, operations: OperationService) -> None:
        self.uow = uow
        self.operations = operations

    async def history(
        self, plugin: str | None = None, *, unfinished: bool = False
    ) -> list[tuple[str, str, str, str]]:
        async with self.uow.read() as repos:
            rows = await repos.operations.history(plugin, unfinished=unfinished)
            return [(r.id, r.plugin, r.action, r.phase) for r in rows]

    async def inspect(self, operation: UUID) -> Result:
        key = str(operation)
        async with self.uow.read() as repos:
            owner, digest, raw, old_receipt = await repos.operations.legacy(key)
            rows = [(s.step_key, s.phase, s.receipt) for s in await repos.operations.steps(key)]
        if raw is None:
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME, message="Missing historical confirmation"
            )
        try:
            snapshot = Snapshot.model_validate_json(raw)
        except ValidationError:
            try:
                body = TypeAdapter(Values).validate_json(raw)
                cap = "operation.recovery"
                provider = self.operations.registry.provider(Requirement(id=cap, version=">=1,<2"))
                identity = StepPlan(
                    step_key="recovery",
                    executor=provider.executor,
                    handler=cap,
                    capability=cap,
                    contract_version="1",
                )
                await anyio.to_thread.run_sync(self.operations.launcher.trust.resolve, identity)
                query = CapabilityRequest(
                    capability=cap,
                    step_key="recovery",
                    payload={
                        "owner": owner,
                        "release_digest": digest,
                        "operation": key,
                        "snapshot": body,
                        "receipt": old_receipt,
                    },
                )
                reply = await self.operations.workers.call(
                    provider,
                    Request(
                        plugin_id=provider.manifest.id,
                        operation_id=operation,
                        method=WorkerMethod.PROVIDER_INSPECT,
                        capability_request=query,
                    ),
                )
                if reply.facts:
                    return Result.model_validate(reply.facts.get("result"))
            except (RuntimeError, OSError, ValueError):
                pass
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Historical evidence requires its original recovery provider.",
            )
        by_key = {s.step_key: s for s in snapshot.plan.steps}
        if not rows or set(by_key) != {s[0] for s in rows}:
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="No confirmed execution steps are available",
            )
        async with acquire(
            self.operations.locks,
            ["operation/" + key, "plugin/" + snapshot.executor.plugin_id],
            wait=False,
        ):
            for step_key, phase, raw_receipt in rows:
                if phase in (StepPhase.PREPARED, StepPhase.VERIFIED):
                    continue
                step = by_key[step_key]
                receipt = StepEvidence.decode(raw_receipt).applied
                async with acquire(
                    self.operations.locks, ["step/" + k for k in step.lock_keys], wait=True
                ):
                    observed = await self.operations.readback(snapshot, step, receipt)
                    await self.operations.ledger.record_verified(operation, step, observed)
                    if observed.outcome not in SUCCESS:
                        return observed
        return Result(
            outcome=Outcome.NEEDS_USER_ACTION,
            message="Readback recorded. Review the recovery plan before changing any resources.",
        )

    async def rollback(self, operation: UUID) -> Snapshot:
        return await self.operations.rollback_plan(operation)

    async def plan(self, operation: UUID) -> ResumePlan:
        proposal = await self.operations.ledger.resume_plan(operation)
        with self.operations.registry.leases(
            {proposal.snapshot.executor, *(s.executor for s in proposal.snapshot.plan.steps)}
        ):
            return proposal

    async def apply(self, plan: ResumePlan, authorize: Authorize | None = None) -> Result:
        return await self.operations.apply(plan.snapshot, authorize, resume=plan)
