"""Commit one evidence-backed state transition per unit of work."""

from uuid import UUID

from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ResourceEffect
from setup_system_sdk.operations import Receipt, Result, Snapshot, StepPlan
from setup_system_sdk.schemas.common import Inputs

from setup_system.domain.operations import SUCCESS, StepEvidence, StepPhase, interrupted
from setup_system.domain.resources import check_reservation, holding
from setup_system.infrastructure.database.uow import UnitOfWork


class ResumePlan(Inputs):
    snapshot: Snapshot
    completed: tuple[str, ...]
    remaining: tuple[str, ...]


class LedgerService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    async def prepare(self, snapshot: Snapshot) -> None:
        await self._prepare(snapshot, rollback=None)

    async def reserve_rollback(self, operation: UUID, plan: Snapshot) -> None:
        if plan.rollback_of != operation:
            raise ValueError("The rollback does not identify its original operation")
        await self._prepare(plan, rollback=operation)

    async def _prepare(self, snapshot: Snapshot, rollback: UUID | None) -> None:
        key = str(snapshot.operation_id)
        plugin = snapshot.executor.plugin_id
        providers = sorted(
            set(snapshot.dependencies)
            | {s.executor.plugin_id for s in snapshot.plan.steps if s.executor.plugin_id != plugin}
        )
        async with self.uow.transaction() as repos:
            if snapshot.action_id == "uninstall" and await repos.dependencies.dependents(plugin):
                raise ValueError("Active or pending operations require this plugin")
            if await repos.dependencies.removals(providers):
                raise ValueError("A required plugin is being removed")
            owners = await repos.resources.owners(snapshot.plan.resources)
            for step in snapshot.plan.steps:
                for row in step.resources:
                    check_reservation(
                        plugin,
                        row,
                        owners.get((row.kind, row.identity), {}),
                        allow_removal=Permission.REMOVE_PREEXISTING in step.permissions,
                    )
            if rollback:
                await repos.operations.set_rollback(str(rollback), "prepared")
            await repos.operations.create(snapshot)
            await repos.resources.reserve_many(key, snapshot.plan.resources)
            await repos.dependencies.stage_requirements(key, plugin, providers)

    async def resume_plan(self, operation: UUID) -> ResumePlan:
        key = str(operation)
        async with self.uow.read() as repos:
            record = await repos.operations.get(key)
            if record.phase == "verified":
                raise ValueError("Operation is already complete")
            snapshot = Snapshot.model_validate_json(await repos.operations.snapshot(key))
            states = await repos.operations.steps(key)
            if not states or [s.step_key for s in states] != [
                step.step_key for step in snapshot.plan.steps
            ]:
                raise ValueError("Incomplete execution identity")
            if any(s.phase not in {StepPhase.PREPARED, StepPhase.VERIFIED} for s in states):
                raise ValueError("Uncertain steps require readback first")
            return ResumePlan(
                snapshot=snapshot,
                completed=tuple(s.step_key for s in states if s.phase == StepPhase.VERIFIED),
                remaining=tuple(s.step_key for s in states if s.phase == StepPhase.PREPARED),
            )

    async def mark_running(self, operation: UUID, step: StepPlan) -> None:
        async with self.uow.transaction() as repos:
            await repos.operations.transition(
                str(operation), step.step_key, ("prepared",), "running"
            )

    async def record_applied(self, operation: UUID, step: StepPlan, receipt: Receipt) -> None:
        key = str(operation)
        receipt.validate_for(operation, step)
        async with self.uow.transaction() as repos:
            confirmed = receipt.result.outcome in SUCCESS - {Outcome.NO_CHANGES} or (
                receipt.result.outcome is Outcome.PARTIAL
                and any(r.verified for r in receipt.result.resources)
            )
            await repos.operations.transition(
                key,
                step.step_key,
                ("running",),
                "applied" if confirmed else "running",
                StepEvidence(applied=receipt).model_dump_json(),
            )
            if confirmed:
                await repos.operations.change_phase(key, receipt.result.resources, "applied")
                await repos.operations.set_outcome(key, "applied", None)

    async def interruption(self, operation: UUID) -> Outcome:
        async with self.uow.read() as repos:
            states = await repos.operations.steps(str(operation))
            effects = [StepEvidence.decode(s.receipt).effect(s.phase) for s in states]
        return interrupted(changed=True in effects, uncertain=None in effects)

    async def record_verified(self, operation: UUID, step: StepPlan, result: Result) -> None:
        receipt = Receipt(
            operation_id=operation, step_key=step.step_key, executor=step.executor, result=result
        )
        receipt.validate_for(operation, step)
        complete = result.outcome in SUCCESS
        if complete and (
            len(result.resources) != len(step.resources)
            or not all(r.verified for r in result.resources)
        ):
            raise ValueError("Readback did not verify every approved resource")
        async with self.uow.transaction() as repos:
            evidence = StepEvidence.decode(
                await repos.operations.receipt(str(operation), step.step_key)
            )
            await repos.operations.transition(
                str(operation),
                step.step_key,
                ("running", "applied", "unresolved"),
                "verified" if complete else "unresolved",
                evidence.model_copy(update={"readback": result}).model_dump_json(),
            )
            await repos.operations.record_evidence(
                str(operation), [r for r in result.resources if r.verified]
            )

    async def finish(self, snapshot: Snapshot, outcome: Outcome) -> None:
        if snapshot.rollback_of:
            await self.finish_rollback(snapshot, outcome)
        else:
            await self._finish(snapshot, outcome)

    async def finish_rollback(self, plan: Snapshot, outcome: Outcome) -> None:
        if not plan.rollback_of:
            raise ValueError("Missing original operation")
        await self._finish(plan, outcome)

    async def _finish(self, snapshot: Snapshot, outcome: Outcome) -> None:
        key = str(snapshot.operation_id)
        plugin = snapshot.executor.plugin_id
        async with self.uow.transaction() as repos:
            saved = await repos.operations.snapshot(key)
            if Snapshot.model_validate_json(saved) != snapshot:
                raise ValueError("The confirmation changed")
            states = await repos.operations.steps(key)
            complete = outcome in SUCCESS
            if complete and any(s.phase != StepPhase.VERIFIED for s in states):
                raise ValueError("An operation with unverified steps cannot succeed")
            if outcome is Outcome.CANCELLED and any(
                StepEvidence.decode(s.receipt).effect(s.phase) is not False for s in states
            ):
                raise ValueError("Execution may already have started")
            verified_steps = {s.step_key for s in states if s.phase == StepPhase.VERIFIED}
            approved = {
                (r.kind, r.identity)
                for s in snapshot.plan.steps
                if s.step_key in verified_steps
                for r in s.resources
            }
            partial = {
                (r.kind, r.identity)
                for step in snapshot.plan.steps
                for r in step.resources
                if any(
                    s.step_key == step.step_key and s.phase == StepPhase.UNRESOLVED for s in states
                )
            }
            evidence = [
                r
                for r in await repos.operations.evidence(key)
                if (r.kind, r.identity) in (approved | partial) and r.verified
            ]
            if not approved.issubset({(r.kind, r.identity) for r in evidence}):
                raise ValueError("Missing verified resource evidence")
            if outcome is not Outcome.CANCELLED:
                owners = await repos.resources.owners(evidence)
                await repos.resources.save_holdings(
                    plugin,
                    [
                        holding(r, owners.get((r.kind, r.identity), {}).get(plugin))
                        for r in evidence
                        if r.effect == ResourceEffect.ENSURE
                    ],
                )
                await repos.resources.remove_holdings(
                    plugin, [r for r in evidence if r.effect == ResourceEffect.REMOVE]
                )
            if snapshot.rollback_of:
                original_key = str(snapshot.rollback_of)
                prior = Snapshot.model_validate_json(await repos.operations.snapshot(original_key))
                restored = {(r.kind, r.identity) for r in evidence}
                await repos.resources.remove_holdings(plugin, evidence)
                await repos.resources.save_holdings(
                    plugin, [row for row in prior.holdings if (row.kind, row.identity) in restored]
                )
                await repos.operations.set_rollback(
                    original_key, "verified" if complete else "unresolved"
                )
            await repos.operations.change_phase(key, evidence, "verified")
            resolved = complete or outcome is Outcome.CANCELLED
            await repos.resources.release(key, None if resolved else evidence)
            if resolved:
                await repos.dependencies.finish(
                    key, plugin, confirm=complete, uninstall=snapshot.action_id == "uninstall"
                )
            await repos.operations.set_outcome(
                key, "verified" if resolved else "unresolved", outcome
            )
