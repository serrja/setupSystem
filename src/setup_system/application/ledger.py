"""Commit one evidence-backed state transition per unit of work."""

from uuid import UUID

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ResourceEffect
from setup_system_sdk.operations import Receipt, Result, StepPlan
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Inputs

from setup_system.domain.confirmation import ConfirmedSnapshot as Snapshot
from setup_system.domain.operations import (
    STEP_PREDECESSORS,
    SUCCESS,
    ChangePhase,
    OperationPhase,
    RollbackPhase,
    StepEvidence,
    StepPhase,
    interrupted,
)
from setup_system.domain.resources import check_reservation, holding, validate_readback
from setup_system.infrastructure.database.uow import Repositories, UnitOfWork


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
            if any(
                r.action == snapshot.action_id
                for r in await repos.operations.history(plugin, unfinished=True)
            ):
                raise ValueError("Resolve the earlier operation for this action before proceeding")
            if (
                snapshot.action_id == LifecycleAction.UNINSTALL
                and await repos.dependencies.dependents(plugin)
            ):
                raise ValueError("Active or pending operations require this plugin")
            if await repos.dependencies.removals(providers):
                raise ValueError("A required plugin is being removed")
            if await repos.resources.reservations(snapshot.plan.resources):
                raise ValueError("Another operation reserves a resource in this plan")
            owners = await repos.resources.owners(snapshot.plan.resources)
            previous = {(r.kind, r.identity): r for r in snapshot.holdings}
            for step in snapshot.plan.steps:
                for row in step.resources:
                    identity = row.kind, row.identity
                    if owners.get(identity, {}).get(plugin) != previous.get(identity):
                        raise ValueError("Resource ownership changed after preview")
                    check_reservation(
                        plugin,
                        row,
                        owners.get((row.kind, row.identity), {}),
                        allow_removal=Permission.REMOVE_PREEXISTING in step.permissions,
                    )
            if rollback:
                original = await repos.operations.get(str(rollback))
                if (
                    original.plugin != plugin
                    or original.phase is not OperationPhase.VERIFIED
                    or original.outcome not in SUCCESS
                ):
                    raise ValueError("Select a successfully completed operation of this plugin")
                if await repos.operations.rollback_phase(str(rollback)) is not None:
                    raise ValueError(
                        "The original operation already has a rollback reservation or outcome"
                    )
                await repos.operations.reserve_rollback(str(rollback))
            await repos.operations.create(snapshot)
            await repos.resources.reserve_many(key, snapshot.plan.resources)
            await repos.dependencies.stage_requirements(key, plugin, providers)

    async def resume_plan(self, operation: UUID) -> ResumePlan:
        key = str(operation)
        async with self.uow.read() as repos:
            record = await repos.operations.get(key)
            if record.phase is OperationPhase.VERIFIED:
                raise ValueError("Operation is already complete")
            snapshot = Snapshot.model_validate_json(await repos.operations.snapshot(key))
            states = await repos.operations.steps(key)
            if [s.step_key for s in states] != [step.step_key for step in snapshot.plan.steps]:
                raise ValueError("Incomplete execution identity")
            if any(s.phase not in {StepPhase.PREPARED, StepPhase.VERIFIED} for s in states):
                raise ValueError("Uncertain steps require readback first")
            return ResumePlan(
                snapshot=snapshot,
                completed=tuple(s.step_key for s in states if s.phase is StepPhase.VERIFIED),
                remaining=tuple(s.step_key for s in states if s.phase is StepPhase.PREPARED),
            )

    async def _confirmed_step(self, repos: Repositories, key: str, step: StepPlan) -> None:
        snapshot = Snapshot.model_validate_json(await repos.operations.snapshot(key))
        if step not in snapshot.plan.steps:
            raise ValueError("The supplied step differs from the stored confirmation")

    async def mark_running(self, operation: UUID, step: StepPlan) -> None:
        async with self.uow.transaction() as repos:
            await self._confirmed_step(repos, str(operation), step)
            await repos.operations.transition(
                str(operation),
                step.step_key,
                STEP_PREDECESSORS[StepPhase.RUNNING],
                StepPhase.RUNNING,
            )

    async def record_applied(self, operation: UUID, step: StepPlan, receipt: Receipt) -> None:
        key = str(operation)
        receipt.validate_for(operation, step)
        async with self.uow.transaction() as repos:
            await self._confirmed_step(repos, key, step)
            if await repos.operations.receipt(key, step.step_key) is not None:
                raise ValueError("The step already has a durable execution receipt")
            confirmed = receipt.result.outcome in SUCCESS - {Outcome.NO_CHANGES} or (
                receipt.result.outcome is Outcome.PARTIAL
                and any(r.verified for r in receipt.result.resources)
            )
            await repos.operations.transition(
                key,
                step.step_key,
                (StepPhase.RUNNING,),
                StepPhase.APPLIED if confirmed else StepPhase.RUNNING,
                StepEvidence(applied=receipt).model_dump_json(),
            )
            if confirmed:
                await repos.operations.change_phase(
                    key, receipt.result.resources, ChangePhase.APPLIED
                )
                await repos.operations.set_outcome(key, OperationPhase.APPLIED, None)

    async def interruption(self, operation: UUID) -> Outcome:
        async with self.uow.read() as repos:
            states = await repos.operations.steps(str(operation))
            effects = [StepEvidence.decode(s.receipt).effect(s.phase) for s in states]
        return interrupted(changed=True in effects, uncertain=None in effects)

    async def record_verified(self, operation: UUID, step: StepPlan, result: Result) -> None:
        key = str(operation)
        async with self.uow.transaction() as repos:
            await self._confirmed_step(repos, key, step)
            evidence = StepEvidence.decode(await repos.operations.receipt(key, step.step_key))
            validate_readback(operation, step, evidence.applied, result)
            phase = StepPhase.VERIFIED if result.outcome in SUCCESS else StepPhase.UNRESOLVED
            published = {
                (c.kind, c.identity)
                for c in await repos.operations.changes(key)
                if c.phase is ChangePhase.VERIFIED
            }
            prior = {(r.kind, r.identity): r for r in await repos.operations.evidence(key)}
            fresh = []
            for row in result.resources:
                if not row.verified:
                    continue
                identity = row.kind, row.identity
                if identity in prior or identity in published:
                    if row != prior.get(identity):
                        raise ValueError(
                            "A previously verified observation changed. Reinspect its origin."
                        )
                else:
                    fresh.append(row)
            await repos.operations.transition(
                key,
                step.step_key,
                STEP_PREDECESSORS[phase],
                phase,
                evidence.model_copy(update={"readback": result}).model_dump_json(),
            )
            await repos.operations.record_evidence(key, fresh)

    async def reserve_resume(self, proposal: ResumePlan) -> None:
        """Recover reservations released by older hosts only while their holdings still match."""
        snapshot = proposal.snapshot
        key = str(snapshot.operation_id)
        plugin = snapshot.executor.plugin_id
        async with self.uow.transaction() as repos:
            op = await repos.operations.get(key)
            if (
                op.phase is OperationPhase.VERIFIED
                or Snapshot.model_validate_json(await repos.operations.snapshot(key)) != snapshot
            ):
                raise ValueError("The recovery confirmation changed")
            reserved = await repos.resources.reservations(snapshot.plan.resources)
            prior = {(r.kind, r.identity): r for r in snapshot.holdings}
            published = {
                (c.kind, c.identity)
                for c in await repos.operations.changes(key)
                if c.phase is ChangePhase.VERIFIED
            }
            observed = {(r.kind, r.identity): r for r in await repos.operations.evidence(key)}
            rollback_prior = {}
            if snapshot.rollback_of:
                source = Snapshot.model_validate_json(
                    await repos.operations.snapshot(str(snapshot.rollback_of))
                )
                rollback_prior = {(r.kind, r.identity): r for r in source.holdings}
            owners = await repos.resources.owners(snapshot.plan.resources)
            missing = []
            for row in snapshot.plan.resources:
                identity = row.kind, row.identity
                if reserved.get(identity) == key:
                    continue
                if identity in reserved or identity not in published or identity not in observed:
                    raise ValueError(
                        "The original reservation is missing or belongs to another operation"
                    )
                current = owners.get(identity, {}).get(plugin)
                expected = (
                    rollback_prior.get(identity)
                    if snapshot.rollback_of
                    else holding(observed[identity], prior.get(identity))
                    if row.effect is ResourceEffect.ENSURE
                    else None
                )
                if current != expected or set(owners.get(identity, {})) - {plugin}:
                    raise ValueError("A later owner or change prevents resuming this operation")
                missing.append(row)
            await repos.resources.reserve_many(key, missing)

    async def finish(self, snapshot: Snapshot, outcome: Outcome) -> None:
        key = str(snapshot.operation_id)
        plugin = snapshot.executor.plugin_id
        async with self.uow.transaction() as repos:
            if Snapshot.model_validate_json(await repos.operations.snapshot(key)) != snapshot:
                raise ValueError("The confirmation changed")
            record = await repos.operations.get(key)
            if record.phase is OperationPhase.VERIFIED:
                if outcome is not record.outcome:
                    raise ValueError("A completed operation cannot change its outcome")
                return
            states = await repos.operations.steps(key)
            if [s.step_key for s in states] != [s.step_key for s in snapshot.plan.steps]:
                raise ValueError("The saved steps differ from the confirmed plan")
            complete = outcome in SUCCESS
            if complete and any(s.phase is not StepPhase.VERIFIED for s in states):
                raise ValueError("An operation with unverified steps cannot succeed")
            if outcome is Outcome.CANCELLED and any(
                StepEvidence.decode(s.receipt).effect(s.phase) is not False for s in states
            ):
                raise ValueError("Execution may already have started")
            published = {
                (c.kind, c.identity)
                for c in await repos.operations.changes(key)
                if c.phase is ChangePhase.VERIFIED
            }
            fresh: list[Resource] = []
            for step, state in zip(snapshot.plan.steps, states, strict=True):
                if state.phase not in (StepPhase.VERIFIED, StepPhase.UNRESOLVED):
                    continue
                evidence = StepEvidence.decode(state.receipt)
                if evidence.readback is None:
                    raise ValueError("Missing step readback")
                pending = [
                    r
                    for r in evidence.readback.resources
                    if r.verified and (r.kind, r.identity) not in published
                ]
                if pending or complete:
                    validate_readback(
                        snapshot.operation_id, step, evidence.applied, evidence.readback
                    )
                    fresh.extend(pending)
            if outcome is not Outcome.CANCELLED:
                await self._publish(repos, snapshot, fresh)
            resolved = complete or outcome is Outcome.CANCELLED
            if snapshot.rollback_of:
                marker = RollbackPhase.VERIFIED if complete else RollbackPhase.UNRESOLVED
                await repos.operations.finish_rollback(
                    str(snapshot.rollback_of), None if outcome is Outcome.CANCELLED else marker
                )
            if resolved:
                await repos.resources.release(key)
                await repos.dependencies.finish(
                    key,
                    plugin,
                    confirm=complete,
                    uninstall=snapshot.action_id == LifecycleAction.UNINSTALL,
                )
            await repos.operations.set_outcome(
                key, OperationPhase.VERIFIED if resolved else OperationPhase.UNRESOLVED, outcome
            )

    async def finish_rollback(self, plan: Snapshot, outcome: Outcome) -> None:
        if not plan.rollback_of:
            raise ValueError("Missing original operation")
        await self.finish(plan, outcome)

    async def _publish(
        self, repos: Repositories, snapshot: Snapshot, evidence: list[Resource]
    ) -> None:
        if not evidence:
            return
        key = str(snapshot.operation_id)
        plugin = snapshot.executor.plugin_id
        reserved = await repos.resources.reservations(evidence)
        owners = await repos.resources.owners(evidence)
        previous = {(r.kind, r.identity): r for r in snapshot.holdings}
        for row in evidence:
            identity = row.kind, row.identity
            if reserved.get(identity) != key or owners.get(identity, {}).get(
                plugin
            ) != previous.get(identity):
                raise ValueError("Publication requires its reservation and the original holding")
        if snapshot.rollback_of:
            source = Snapshot.model_validate_json(
                await repos.operations.snapshot(str(snapshot.rollback_of))
            )
            restored = {(r.kind, r.identity) for r in evidence}
            await repos.resources.remove_holdings(plugin, evidence)
            await repos.resources.save_holdings(
                plugin, [r for r in source.holdings if (r.kind, r.identity) in restored]
            )
        else:
            await repos.resources.save_holdings(
                plugin,
                [
                    holding(r, previous.get((r.kind, r.identity)))
                    for r in evidence
                    if r.effect is ResourceEffect.ENSURE
                ],
            )
            await repos.resources.remove_holdings(
                plugin, [r for r in evidence if r.effect is ResourceEffect.REMOVE]
            )
        await repos.operations.change_phase(key, evidence, ChangePhase.VERIFIED)
