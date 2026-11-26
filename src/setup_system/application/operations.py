"""Plan, revalidate and supervise ordered steps outside ledger transactions."""

from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import anyio
from anyio.abc import TaskGroup
from setup_system_sdk.enums.operation import ActionMode, Outcome, WorkerMethod
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.operations import Plan, Receipt, Result, StepPlan
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.wire import Request

from setup_system.application.authorization import AuthorizationRequest, Authorize, before_execution
from setup_system.application.ledger import LedgerService, ResumePlan
from setup_system.application.providers import ProviderBroker
from setup_system.application.rollback import RollbackPlanner
from setup_system.configuration.profiles import SavedValues
from setup_system.domain.confirmation import ConfirmedSnapshot as Snapshot
from setup_system.domain.confirmation import ProviderBinding
from setup_system.domain.operations import SUCCESS
from setup_system.errors import OperationError
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
        self.rollback = RollbackPlanner(ledger, registry, workers)
        self.locks = locks
        self.launcher = launcher
        self.tasks = tasks
        self.active: dict[str, UUID] = {}
        self.finished: dict[UUID, anyio.Event] = {}
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
        return await self._preview(plugin_id, action_id, overrides, inputs, operation_id, None)

    async def _preview(
        self,
        plugin_id: str,
        action_id: str,
        overrides: Values,
        inputs: Values,
        operation_id: UUID | None,
        historical: Snapshot | None,
    ) -> Snapshot:
        release = (
            await anyio.to_thread.run_sync(self.registry.resolve_executor, historical.executor)
            if historical
            else await anyio.to_thread.run_sync(self.registry.resolve, plugin_id)
        )
        action = next((a for a in release.manifest.actions if a.id == action_id), None)
        if not action:
            raise ValueError("Unknown plugin action")
        key = operation_id or uuid4()
        document = (
            SavedValues(
                historical.settings, historical.settings_revision, release.manifest.settings_version
            )
            if historical
            else await self.profile(plugin_id)
        )
        async with self.ledger.uow.read() as repos:
            holdings = tuple(await repos.resources.list_holdings(plugin_id))
        approved: list[StepPlan] = []
        dependencies = set(release.manifest.dependencies)
        bindings: dict[str, ProviderBinding] = {}
        try:
            for plugin in dependencies if not historical else ():
                await anyio.to_thread.run_sync(self.registry.resolve, plugin)
            providers = (
                self.broker.retained(action, historical)
                if historical
                else await anyio.to_thread.run_sync(self.broker.resolve, action)
            )
            bindings = {
                cap: ProviderBinding(
                    executor=p.executor,
                    contract_version=next(c.version for c in p.manifest.provides if c.id == cap),
                )
                for cap, p in providers.items()
            }
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
                    overrides=historical.settings if historical else overrides,
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
                        await anyio.to_thread.run_sync(
                            self.launcher.trust.resolve, step, bindings, plan.steps
                        )
                    except (OSError, ValueError):
                        plan = Plan(
                            outcome=Outcome.BLOCKED,
                            summary="Check the native executor and its approved provider releases.",
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
            providers=bindings,
            holdings=holdings,
        )

    async def rollback_plan(self, original: UUID, *, operation_id: UUID | None = None) -> Snapshot:
        return await self.rollback.plan(original, operation_id=operation_id)

    async def _replan(self, snapshot: Snapshot, *, historical: bool = False) -> Snapshot:
        if snapshot.rollback_of:
            return await self.rollback_plan(
                snapshot.rollback_of, operation_id=snapshot.operation_id
            )
        return await self._preview(
            snapshot.executor.plugin_id,
            snapshot.action_id,
            snapshot.overrides,
            snapshot.inputs,
            snapshot.operation_id,
            snapshot if historical else None,
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
        plugin = snapshot.executor.plugin_id
        if snapshot.operation_id in self.pending or plugin in self.active:
            raise ValueError("This operation is already running")
        done = anyio.Event()
        token = anyio.Event()
        self.pending[snapshot.operation_id] = token
        self.active[plugin] = snapshot.operation_id
        self.finished[snapshot.operation_id] = done
        results: list[Result | Exception] = []

        def finished() -> None:
            self.active.pop(plugin, None)
            self.finished.pop(snapshot.operation_id, None)
            self.pending.pop(snapshot.operation_id, None)
            done.set()

        async def run() -> None:
            try:
                results.append(await self._apply(snapshot, authorize, token, resume))
            except Exception as error:
                results.append(error)
            finally:
                finished()

        try:
            self.tasks.start_soon(run)
        except RuntimeError:
            finished()
            raise
        await done.wait()
        if not results:
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Inspect the ledger after the interrupted execution.",
            )
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
        identities = snapshot.executors()
        lock_keys = ["operation/" + str(key), *("plugin/" + p.plugin_id for p in identities)]
        async with acquire(self.locks, lock_keys, wait=False):
            with self.registry.leases(identities):
                completed: tuple[str, ...] = ()
                if resume is None:
                    fresh = await before_execution(self._replan(snapshot), token)
                    if fresh is None or token.is_set():
                        return Result(
                            outcome=Outcome.CANCELLED, message="Stopped before reserving the plan."
                        )
                    if fresh.plan.digest() != snapshot.plan.digest() or fresh != snapshot:
                        raise ValueError("The confirmed plan changed. Preview again.")
                    if snapshot.rollback_of:
                        await self.ledger.reserve_rollback(snapshot.rollback_of, snapshot)
                    else:
                        await self.ledger.prepare(snapshot)
                else:
                    if resume.snapshot != snapshot or await self.ledger.resume_plan(key) != resume:
                        raise ValueError("The recovery confirmation changed")
                    await self.ledger.reserve_resume(resume)
                    completed = resume.completed
                preparation_error = None
                for step in snapshot.plan.steps:
                    if step.step_key in completed:
                        continue
                    async with acquire(
                        self.locks,
                        ["step/" + k for k in step.lock_keys],
                        wait=True,
                        cancelled=token,
                    ) as acquired:
                        if not acquired or token.is_set():
                            break
                        try:
                            fresh = await before_execution(
                                self._replan(snapshot, historical=resume is not None), token
                            )
                            if fresh is None or token.is_set():
                                break
                            if (
                                step not in fresh.plan.steps
                                or (
                                    bool(snapshot.providers)
                                    and fresh.providers != snapshot.providers
                                )
                                or fresh.settings_revision != snapshot.settings_revision
                            ):
                                break
                            secret = None
                            record = None
                            if PRIVILEGED.intersection(step.permissions):
                                record = await anyio.to_thread.run_sync(
                                    self.launcher.trust.resolve,
                                    step,
                                    snapshot.providers,
                                    snapshot.plan.steps,
                                )
                                if authorize:
                                    secret = await before_execution(
                                        authorize(AuthorizationRequest(key, step.step_key, record)),
                                        token,
                                    )
                                if secret is None:
                                    break
                        except OperationError as error:
                            preparation_error = error
                            break
                        except (RuntimeError, OSError, ValueError):
                            break
                        if token.is_set():
                            break
                        await self.ledger.mark_running(key, step)
                        release = self.registry.resolve_executor(step.executor)
                        req = Request(
                            plugin_id=release.manifest.id,
                            operation_id=key,
                            action_id=snapshot.action_id,
                            snapshot=snapshot.worker_snapshot(),
                            step_key=step.step_key,
                            method=WorkerMethod.EXECUTE,
                        )
                        receipt = None
                        try:
                            native = (
                                partial(
                                    self.launcher.execute,
                                    record,
                                    snapshot.worker_snapshot(),
                                    step,
                                    secret,
                                    token,
                                    snapshot.providers,
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
                    except OperationError as error:
                        result = Result(
                            outcome=Outcome.UNKNOWN_OUTCOME, message=error.detail.message
                        )
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
                if preparation_error is not None:
                    raise preparation_error
                return Result(
                    outcome=outcome, message="Execution stopped. Preview the remaining changes."
                )

    async def _verify(self, snapshot: Snapshot) -> Result:
        plugin = snapshot.executor.plugin_id
        key = snapshot.operation_id
        release = self.registry.resolve_executor(snapshot.executor)
        if snapshot.rollback_of:
            for step in snapshot.plan.steps:
                result = await self.readback(snapshot, step, None)
                if result.outcome not in SUCCESS:
                    return result
            return Result(outcome=Outcome.COMPLETE, message="Original file contents verified")
        action = next(a for a in release.manifest.actions if a.id == snapshot.action_id)
        providers = self.broker.retained(action, snapshot)
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
            route=self.broker.route(key, providers, [], snapshot.holdings, inspect_only=True),
        )
        return reply.result or Result(
            outcome=Outcome.UNKNOWN_OUTCOME, message="Missing final verification"
        )

    async def readback(self, snapshot: Snapshot, step: StepPlan, receipt: Receipt | None) -> Result:
        try:
            release = self.registry.resolve_executor(step.executor)
            route = None
            if not step.capability and not snapshot.rollback_of:
                action = next(a for a in release.manifest.actions if a.id == snapshot.action_id)
                providers = self.broker.retained(action, snapshot)
                route = self.broker.route(
                    snapshot.operation_id, providers, [], snapshot.holdings, inspect_only=True
                )
            reply = await self.workers.call(
                release,
                Request(
                    plugin_id=release.manifest.id,
                    operation_id=snapshot.operation_id,
                    method=WorkerMethod.READBACK,
                    snapshot=snapshot.worker_snapshot(),
                    step_key=step.step_key,
                    receipt=receipt,
                ),
                route=route,
            )
            if reply.result:
                return reply.result
        except OperationError as error:
            return Result(outcome=Outcome.UNKNOWN_OUTCOME, message=error.detail.message)
        except ValueError as error:
            return Result(outcome=Outcome.UNKNOWN_OUTCOME, message=str(error))
        except (RuntimeError, OSError):
            pass
        return Result(
            outcome=Outcome.UNKNOWN_OUTCOME,
            message="The executor could not confirm the step outcome",
        )
