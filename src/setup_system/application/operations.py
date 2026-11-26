"""Plan, revalidate and supervise ordered steps outside ledger transactions."""

from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from anyio.abc import TaskGroup
from setup_system_sdk.enums.operation import ActionMode, Outcome, WorkerMethod
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.operations import Plan, Receipt, Result, Snapshot, StepPlan
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.wire import Request

from setup_system.application.authorization import AuthorizationRequest, Authorize
from setup_system.application.ledger import LedgerService, ResumePlan
from setup_system.application.providers import ProviderBroker
from setup_system.configuration.profiles import SavedValues
from setup_system.domain.operations import SUCCESS, StepEvidence
from setup_system.infrastructure.locks import acquire
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient
from setup_system.infrastructure.trust import NativeLauncher

PRIVILEGED = frozenset({Permission.ROOT, Permission.SU, Permission.SUDO})


class OperationService:
    def __init__(
        self,
        registry: PluginRegistry,
        broker: ProviderBroker,
        workers: WorkerClient,
        profile: Callable[[str], Awaitable[SavedValues]],
        ledger: LedgerService,
        locks: Path,
        launcher: NativeLauncher,
        tasks: TaskGroup,
    ) -> None:
        self.registry = registry
        self.broker = broker
        self.workers = workers
        self.profile = profile
        self.ledger = ledger
        self.locks = locks
        self.launcher = launcher
        self.tasks = tasks
        self.pending: dict[UUID, anyio.Event] = {}

    async def preview(
        self,
        plugin_id: str,
        action_id: str,
        overrides: Values,
        inputs: Values,
        *,
        operation_id: UUID | None = None,
    ) -> Snapshot:
        release = await anyio.to_thread.run_sync(self.registry.resolve, plugin_id)
        action = next((a for a in release.manifest.actions if a.id == action_id), None)
        if not action:
            raise ValueError("Unknown plugin action")
        key = operation_id or uuid4()
        document = await self.profile(plugin_id)
        async with self.ledger.uow.read() as repos:
            holdings = tuple(await repos.resources.list_holdings(plugin_id))
        approved: list[StepPlan] = []
        dependencies = set(release.manifest.dependencies)
        try:
            for plugin in dependencies:
                await anyio.to_thread.run_sync(self.registry.resolve, plugin)
            providers = await anyio.to_thread.run_sync(self.broker.resolve, action)
        except ValueError as error:
            plan = Plan(outcome=Outcome.BLOCKED, summary=str(error))
            values = document.values
        else:
            dependencies.update(
                p.manifest.id for p in providers.values() if p.manifest.id != plugin_id
            )
            route = self.broker.route(key, providers, approved, holdings)
            reply = await self.workers.call(
                release,
                Request(
                    plugin_id=plugin_id,
                    operation_id=key,
                    method=WorkerMethod.PLAN,
                    action_id=action_id,
                    saved=document.values,
                    overrides=overrides,
                    inputs=inputs,
                    holdings=holdings,
                ),
                route=route,
            )
            if reply.plan is None:
                raise ValueError("Worker returned no plan")
            plan = reply.plan
            values = reply.settings
            if action.mode in {ActionMode.READ_ONLY, ActionMode.MANUAL} and plan.steps:
                raise ValueError("A read-only action cannot declare executable steps")
            for step in plan.steps:
                if step.permissions - set(action.permissions):
                    raise ValueError("Step requests permissions outside this action")
                if step.capability:
                    if step not in approved:
                        raise ValueError("Provider did not plan this step")
                elif step.executor != release.executor or step.handler != action_id:
                    raise ValueError("Unexpected local step executor or handler")
                if PRIVILEGED.intersection(step.permissions):
                    try:
                        await anyio.to_thread.run_sync(self.launcher.trust.resolve, step)
                    except (OSError, ValueError):
                        plan = Plan(
                            outcome=Outcome.BLOCKED,
                            summary="The approved native executor is unavailable",
                        )
                        break
        return Snapshot(
            operation_id=key,
            executor=release.executor,
            action_id=action_id,
            settings_revision=document.revision,
            settings=values,
            overrides=overrides,
            inputs=inputs,
            plan=plan,
            dependencies=tuple(sorted(dependencies)),
            holdings=holdings,
        )

    async def rollback_plan(self, original: UUID, *, operation_id: UUID | None = None) -> Snapshot:
        async with self.ledger.uow.read() as repos:
            source = Snapshot.model_validate_json(await repos.operations.snapshot(str(original)))
            operation = await repos.operations.get(str(original))
            if (
                source.rollback_of
                or operation.phase != "verified"
                or operation.outcome == Outcome.CANCELLED
            ):
                raise ValueError("Select a completed original operation")
            evidence = {
                row.step_key: row.receipt for row in await repos.operations.steps(str(original))
            }
            holdings = tuple(await repos.resources.list_holdings(source.executor.plugin_id))
        release = await anyio.to_thread.run_sync(self.registry.resolve, source.executor.plugin_id)
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
                        snapshot=source,
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

    async def _replan(self, snapshot: Snapshot) -> Snapshot:
        if snapshot.rollback_of:
            return await self.rollback_plan(
                snapshot.rollback_of, operation_id=snapshot.operation_id
            )
        return await self.preview(
            snapshot.executor.plugin_id,
            snapshot.action_id,
            snapshot.overrides,
            snapshot.inputs,
            operation_id=snapshot.operation_id,
        )

    def cancel(self, operation: UUID) -> None:
        token = self.pending.get(operation)
        if token:
            token.set()

    async def apply(
        self,
        snapshot: Snapshot,
        authorize: Authorize | None = None,
        *,
        resume: ResumePlan | None = None,
    ) -> Result:
        if snapshot.operation_id in self.pending:
            raise ValueError("This operation is already running")
        done = anyio.Event()
        token = anyio.Event()
        self.pending[snapshot.operation_id] = token
        results: list[Result | Exception] = []

        async def run() -> None:
            try:
                results.append(await self._apply(snapshot, authorize, token, resume))
            except Exception as error:
                results.append(error)
            finally:
                self.pending.pop(snapshot.operation_id, None)
                done.set()

        self.tasks.start_soon(run)
        await done.wait()
        result = results[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def _apply(
        self,
        snapshot: Snapshot,
        authorize: Authorize | None,
        token: anyio.Event,
        resume: ResumePlan | None,
    ) -> Result:
        key = snapshot.operation_id
        if snapshot.plan.outcome not in {Outcome.READY, Outcome.NO_CHANGES}:
            raise ValueError("This plan cannot be applied")
        plugin = snapshot.executor.plugin_id
        async with acquire(self.locks, ["operation/" + str(key), "plugin/" + plugin], wait=False):
            with self.registry.leases(
                {snapshot.executor, *(s.executor for s in snapshot.plan.steps)}
            ):
                completed: tuple[str, ...] = ()
                if resume is None:
                    fresh = await self._replan(snapshot)
                    if fresh.plan.digest() != snapshot.plan.digest() or fresh != snapshot:
                        raise ValueError("The confirmed plan changed. Preview again.")
                    if snapshot.rollback_of:
                        await self.ledger.reserve_rollback(snapshot.rollback_of, snapshot)
                    else:
                        await self.ledger.prepare(snapshot)
                else:
                    if resume.snapshot != snapshot or await self.ledger.resume_plan(key) != resume:
                        raise ValueError("The recovery confirmation changed")
                    completed = resume.completed
                for step in snapshot.plan.steps:
                    if step.step_key in completed:
                        continue
                    async with acquire(
                        self.locks, ["step/" + k for k in step.lock_keys], wait=True
                    ):
                        if token.is_set():
                            break
                        try:
                            fresh = await self._replan(snapshot)
                            if (
                                step not in fresh.plan.steps
                                or fresh.settings_revision != snapshot.settings_revision
                            ):
                                break
                            secret = None
                            record = None
                            if PRIVILEGED.intersection(step.permissions):
                                record = await anyio.to_thread.run_sync(
                                    self.launcher.trust.resolve, step
                                )
                                if authorize:
                                    secret = await authorize(
                                        AuthorizationRequest(key, step.step_key, record)
                                    )
                                if secret is None:
                                    break
                        except (RuntimeError, OSError, ValueError):
                            break
                        if token.is_set():
                            break
                        await self.ledger.mark_running(key, step)
                        release = self.registry.resolve(step.executor.plugin_id)
                        req = Request(
                            plugin_id=release.manifest.id,
                            operation_id=key,
                            action_id=snapshot.action_id,
                            snapshot=snapshot,
                            step_key=step.step_key,
                            method=WorkerMethod.EXECUTE,
                        )
                        receipt = None
                        try:
                            native = (
                                partial(
                                    self.launcher.execute, record, snapshot, step, secret, token
                                )
                                if record and secret
                                else None
                            )
                            receipt = (
                                await self.workers.call(
                                    release, req, execute=True, cancel=token, native=native
                                )
                            ).receipt
                            if receipt:
                                await self.ledger.record_applied(key, step, receipt)
                        except Exception:
                            receipt = None
                        result = await self.readback(snapshot, step, receipt)
                        try:
                            await self.ledger.record_verified(key, step, result)
                        except ValueError:
                            result = Result(
                                outcome=Outcome.UNKNOWN_OUTCOME,
                                message="Readback exceeded the confirmed step",
                            )
                            await self.ledger.record_verified(key, step, result)
                        if result.outcome not in SUCCESS:
                            outcome = (
                                Outcome.PARTIAL
                                if result.outcome == Outcome.PARTIAL
                                and any(r.verified for r in result.resources)
                                else Outcome.UNKNOWN_OUTCOME
                            )
                            await self.ledger.finish(snapshot, outcome)
                            return result.model_copy(update={"outcome": outcome})
                else:
                    try:
                        result = await self._verify(snapshot)
                    except (RuntimeError, ValueError, OSError):
                        result = Result(
                            outcome=Outcome.UNKNOWN_OUTCOME,
                            message="The final verification requires recovery",
                        )
                    if result.outcome in {Outcome.CANCELLED, Outcome.FAILED, Outcome.BLOCKED}:
                        result = result.model_copy(
                            update={"outcome": await self.ledger.interruption(key)}
                        )
                    await self.ledger.finish(snapshot, result.outcome)
                    return result
                outcome = await self.ledger.interruption(key)
                await self.ledger.finish(snapshot, outcome)
                return Result(
                    outcome=outcome, message="Execution stopped. Preview the remaining changes."
                )

    async def _verify(self, snapshot: Snapshot) -> Result:
        plugin = snapshot.executor.plugin_id
        key = snapshot.operation_id
        release = self.registry.resolve(plugin)
        if snapshot.rollback_of:
            for step in snapshot.plan.steps:
                result = await self.readback(snapshot, step, None)
                if result.outcome not in SUCCESS:
                    return result
            return Result(outcome=Outcome.COMPLETE, message="Original file contents verified")
        action = next(a for a in release.manifest.actions if a.id == snapshot.action_id)
        providers = self.broker.resolve(action)
        reply = await self.workers.call(
            release,
            Request(
                plugin_id=plugin,
                operation_id=key,
                action_id=snapshot.action_id,
                method=WorkerMethod.VERIFY,
                saved=snapshot.settings,
                inputs=snapshot.inputs,
                plan=snapshot.plan,
                holdings=snapshot.holdings,
            ),
            route=self.broker.route(key, providers, [], snapshot.holdings),
        )
        return reply.result or Result(
            outcome=Outcome.UNKNOWN_OUTCOME, message="Missing final verification"
        )

    async def readback(self, snapshot: Snapshot, step: StepPlan, receipt: Receipt | None) -> Result:
        try:
            release = self.registry.resolve(step.executor.plugin_id)
            if release.executor != step.executor:
                raise ValueError("Executor identity changed")
            reply = await self.workers.call(
                release,
                Request(
                    plugin_id=release.manifest.id,
                    operation_id=snapshot.operation_id,
                    method=WorkerMethod.READBACK,
                    snapshot=snapshot,
                    step_key=step.step_key,
                    receipt=receipt,
                ),
            )
            if reply.result:
                return reply.result
        except Exception:
            pass
        return Result(
            outcome=Outcome.UNKNOWN_OUTCOME,
            message="The executor could not confirm the step outcome",
        )
