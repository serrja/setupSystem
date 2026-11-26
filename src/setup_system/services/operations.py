"""Plan commands, reserve resources, execute once and persist a separate readback."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

from setup_system.errors import OperationError
from setup_system.execution.authorization import BecomePassword, _environment
from setup_system.execution.make_transport import make_command
from setup_system.execution.native_runner import (
    NativeRunner,
    Snapshot,
    descriptor,
    protected_resources,
)
from setup_system.execution.worker import pipe, query
from setup_system.services.extension_lifecycle import generation, generation_lock
from setup_system.services.extensions import Activation, active
from setup_system.services.rollback import RecoveryPlan
from setup_system.services.settings import SettingsStore
from setup_system.settings import Settings
from setup_system.storage.repositories.extensions import ExtensionRepository
from setup_system.storage.repositories.operations import OperationRepository
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import ActionMode, Outcome, WorkerMethod
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import RecoveryStep, ResourceEffect, ResourceOwnership
from setup_system_sdk.schemas.common import ActionId, Values
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.manifest import Action
from setup_system_sdk.schemas.operation import Mutation, Plan, Result
from setup_system_sdk.schemas.wire import Request

PRIVILEGED = frozenset({Permission.ROOT, Permission.SU, Permission.SUDO})
VERIFIED = frozenset({Outcome.CHANGED, Outcome.COMPLETE, Outcome.NEEDS_REBOOT, Outcome.NO_CHANGES})


def track_existing(action: Action, plan: Plan) -> bool:
    return (
        action.id in (LifecycleAction.INSTALL, LifecycleAction.UPDATE)
        and plan.outcome is Outcome.NO_CHANGES
        and all(
            row.effect is ResourceEffect.ENSURE
            and row.before == row.after
            and row.ownership in (ResourceOwnership.PREEXISTING, ResourceOwnership.SHARED)
            for row in plan.resources
        )
    )


def can_review(action: Action, plan: Plan) -> bool:
    if action.mode in (ActionMode.MANUAL, ActionMode.READ_ONLY):
        return plan.outcome in (
            Outcome.READY,
            Outcome.NEEDS_USER_ACTION,
            Outcome.NO_CHANGES,
            Outcome.COMPLETE,
        )
    return action.target is not None and (
        plan.outcome is Outcome.READY or track_existing(action, plan)
    )


class Operations:
    def __init__(
        self,
        options: Settings,
        env_root: Path,
        store: SettingsStore,
        records: OperationRepository,
        extensions: ExtensionRepository,
    ) -> None:
        self.options = options
        self.env_root = env_root
        self.store = store
        self.records = records
        self.extensions = extensions
        self.runner = NativeRunner(options)
        self._jobs: dict[UUID, asyncio.Task[Result]] = {}
        self._cancel: dict[UUID, tuple[str, asyncio.Event]] = {}

    async def activation(self, plugin_id: str) -> Activation:
        release = await asyncio.to_thread(active, self.env_root, plugin_id)
        if release is None or release.digest != await self.extensions.active(plugin_id):
            raise OperationError(
                Error(
                    code=ErrorCode.INCOMPATIBLE_PLUGIN,
                    message="Activate the reviewed extension release before running this action.",
                )
            )
        return release

    async def preview(
        self,
        plugin_id: str,
        action_id: ActionId,
        overrides: Values,
        inputs: Values,
        *,
        saved: Values | None = None,
        revision: str | None = None,
    ) -> Snapshot:
        release = await self.activation(plugin_id)
        descriptor(release, action_id)
        document = await self.store.read(plugin_id)
        if revision is not None and revision != document.revision:
            raise OperationError(
                Error(
                    code=ErrorCode.STALE_PLAN, message="Settings changed. Reload the saved values."
                )
            )
        holdings = tuple(await self.records.holdings(plugin_id))
        operation_id = uuid4()
        reply = await query(
            release,
            Request(
                operation_id=operation_id,
                method=WorkerMethod.PLAN,
                plugin_id=plugin_id,
                action_id=action_id,
                saved=document.values if saved is None else saved,
                overrides=overrides,
                inputs=inputs,
                holdings=holdings,
            ),
            self.options,
        )
        if reply.plan is None:
            raise OperationError(
                Error(
                    code=ErrorCode.INCOMPATIBLE_PLUGIN,
                    message="The extension returned no action plan.",
                    operation_id=operation_id,
                )
            )
        plan = reply.plan
        if action_id == LifecycleAction.UNINSTALL:
            dependents = await self.records.dependents(plugin_id)
            if dependents:
                plan = Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Required by installed extensions: " + ", ".join(dependents),
                )
        return Snapshot(
            operation_id=operation_id,
            plugin_id=plugin_id,
            digest=release.digest,
            settings_revision=document.revision,
            mutation=Mutation(
                action_id=action_id,
                holdings=holdings,
                inputs=inputs,
                settings=reply.settings,
                plan=plan,
            ),
        )

    async def apply(self, snapshot: Snapshot, password: BecomePassword | None = None) -> Result:
        key = snapshot.operation_id
        if key in self._jobs:
            if password:
                password.discard()
            return await asyncio.shield(self._jobs[key])
        token = asyncio.Event()

        async def perform() -> Result:
            try:
                async with (
                    self.records.claim_extension(snapshot.plugin_id),
                    self.records.claim_reconciliation(snapshot.operation_id),
                ):
                    return await self._execute(snapshot, password, token)
            finally:
                if password:
                    password.discard()
                self._jobs.pop(key, None)
                self._cancel.pop(key, None)

        task = asyncio.create_task(perform())
        self._jobs[key] = task
        self._cancel[key] = (snapshot.plugin_id, token)
        return await asyncio.shield(task)

    def cancel(self, plugin: str) -> int:
        tokens = [token for owner, token in self._cancel.values() if owner == plugin]
        for token in tokens:
            token.set()
        return len(tokens)

    async def drain(self) -> None:
        if self._jobs:
            await asyncio.gather(*tuple(self._jobs.values()), return_exceptions=True)

    async def _execute(
        self,
        snapshot: Snapshot,
        password: BecomePassword | None,
        token: asyncio.Event,
    ) -> Result:
        plugin = snapshot.plugin_id
        release = await self.activation(plugin)
        with generation_lock(generation(release), exclusive=False):
            item = snapshot.mutation
            action = descriptor(release, item.action_id)
            current = await self.store.read(plugin)
            if (
                snapshot.digest != release.digest
                or current.revision != snapshot.settings_revision
                or not can_review(action, item.plan)
            ):
                return Result(
                    outcome=Outcome.BLOCKED,
                    message="Release, settings or readiness changed. Preview again.",
                )
            holdings = tuple(await self.records.holdings(plugin))
            request = Request(
                operation_id=snapshot.operation_id,
                plugin_id=plugin,
                action_id=action.id,
                method=WorkerMethod.PLAN,
                holdings=holdings,
                inputs=item.inputs,
                overrides=item.settings,
            )
            fresh = await query(release, request, self.options)
            if (
                holdings != item.holdings
                or fresh.plan != item.plan
                or fresh.settings != item.settings
            ):
                return Result(
                    outcome=Outcome.BLOCKED, message="Observed state changed. Preview again."
                )
            existing = track_existing(action, item.plan)
            privileged = (
                bool(PRIVILEGED.intersection(action.permissions))
                and action.mode in (ActionMode.MUTATION, ActionMode.AUTHENTICATED_CHECK)
                and not existing
            )
            if privileged:
                await asyncio.to_thread(protected_resources, release)
                if password is None:
                    return Result(
                        outcome=Outcome.BLOCKED,
                        message="This action requires native authorization.",
                    )
            if token.is_set():
                return Result(outcome=Outcome.CANCELLED, message="Cancelled before execution.")
            if action.mode is not ActionMode.MUTATION:
                try:
                    if action.mode is ActionMode.AUTHENTICATED_CHECK:
                        inspection = await self.runner.execute(release, snapshot, password, token)
                    elif action.mode is ActionMode.READ_ONLY and item.plan.outcome is Outcome.READY:
                        inspection = await self._verify(release, request, item.plan)
                    else:
                        inspection = Result(
                            outcome=Outcome.NEEDS_USER_ACTION
                            if action.mode is ActionMode.MANUAL
                            else item.plan.outcome,
                            message=item.plan.summary,
                            resources=item.plan.resources,
                        )
                except (OperationError, ValueError, OSError, TimeoutError, ExceptionGroup):
                    inspection = Result(
                        outcome=Outcome.FAILED, message="The requested inspection was unavailable."
                    )
                await self.records.record_inspection(
                    snapshot.operation_id,
                    release.manifest,
                    release.digest,
                    action.id,
                    snapshot.settings_revision,
                    item.inputs,
                    item.settings,
                    item.plan,
                    inspection,
                )
                return inspection
            try:
                await self.records.reserve(
                    snapshot.operation_id,
                    release.manifest,
                    release.digest,
                    action.id,
                    settings_revision=current.revision,
                    inputs=item.inputs,
                    settings=item.settings,
                    plan=item.plan,
                    holdings=holdings,
                    allow_preexisting_removal=(
                        (Permission.REMOVE_PREEXISTING in action.permissions and privileged)
                        or (
                            Permission.REMOVE_SELECTED in action.permissions
                            and action.cleanup_phase is None
                            and len(item.plan.resources) == 1
                            and item.plan.resources[0].effect is ResourceEffect.REMOVE
                            and bool(item.plan.resources[0].before)
                        )
                    ),
                )
            except ValueError:
                return Result(
                    outcome=Outcome.BLOCKED,
                    message="This operation conflicts with a reservation or existing ownership.",
                )
            if existing:
                try:
                    result = await self._verify(release, request, item.plan)
                except (OperationError, ValueError, OSError):
                    result = Result(
                        outcome=Outcome.UNKNOWN_OUTCOME,
                        message="Could not confirm the already-installed resources.",
                    )
            else:
                try:
                    native = await self.runner.execute(release, snapshot, password, token)
                    result = native
                    observed = [r for r in native.resources if r.verified]
                    if observed or native.recovery_id:
                        await self.records.record_readback(
                            snapshot.operation_id, observed, native.recovery_id
                        )
                    if native.outcome in VERIFIED and not (
                        privileged
                        and observed
                        and len(observed) == len(native.resources)
                        and {(r.kind, r.identity) for r in observed}
                        == {(r.kind, r.identity) for r in item.plan.resources}
                    ):
                        checked = await self._verify(release, request, item.plan)
                        events = (
                            *native.events,
                            *(e for e in checked.events if e not in native.events),
                        )
                        result = checked.model_copy(
                            update={"recovery_id": native.recovery_id, "events": events}
                        )
                except (Exception, asyncio.CancelledError):
                    result = Result(
                        outcome=Outcome.UNKNOWN_OUTCOME,
                        message="Execution was interrupted. Inspect the resources before retrying.",
                    )
            try:
                await self.records.finish(snapshot.operation_id, result)
            except ValueError:
                result = Result(
                    outcome=Outcome.UNKNOWN_OUTCOME,
                    message="Readback differed from the confirmed plan. Inspect the operation.",
                )
                await self.records.finish(snapshot.operation_id, result)
            return result

    async def _verify(self, release: Activation, request: Request, plan: Plan) -> Result:
        reply = await query(
            release,
            request.model_copy(update={"method": WorkerMethod.VERIFY, "plan": plan}),
            self.options,
        )
        if reply.result is None or reply.result.outcome is Outcome.READY:
            raise OperationError(
                Error(
                    code=ErrorCode.UNKNOWN_OUTCOME,
                    message="The extension did not confirm the outcome.",
                    operation_id=request.operation_id,
                )
            )
        return reply.result

    async def reconcile(self, plugin: str) -> list[Result]:
        release = await self.activation(plugin)
        results = []
        pending = set(await self.records.pending(plugin))
        readbacks = await self.records.pending_readbacks(plugin)
        if pending.difference(readbacks):
            results.append(
                Result(
                    outcome=Outcome.PARTIAL,
                    message="An interrupted keyring change or file restore requires manual review. "
                    "Inspect its journal entry and recovery receipt before continuing.",
                )
            )
        for key in readbacks:
            if key in self._jobs:
                continue
            try:
                async with self.records.claim_reconciliation(key):
                    saved = await self.records.snapshot(key)
                    if saved.digest != release.digest:
                        results.append(
                            Result(
                                outcome=Outcome.BLOCKED,
                                message="Readback requires the originally reviewed extension.",
                            )
                        )
                        continue
                    result = await self._verify(
                        release,
                        Request(
                            operation_id=key,
                            plugin_id=plugin,
                            action_id=saved.action_id,
                            method=WorkerMethod.VERIFY,
                            overrides=saved.settings,
                            inputs=saved.inputs,
                            holdings=tuple(saved.holdings),
                        ),
                        saved.plan,
                    )
                    if result.outcome in VERIFIED:
                        await self.records.finish(key, result, recovered=True)
                    results.append(result)
            except ValueError:
                results.append(
                    Result(
                        outcome=Outcome.BLOCKED,
                        message="The operation is busy or lacks proof of the original change.",
                    )
                )
        return results

    async def rollback(self, plugin: str, operation: UUID, confirmed: RecoveryPlan) -> Result:
        async with self.records.claim_extension(plugin):
            return await self._restore(plugin, operation, confirmed)

    async def _restore(self, plugin: str, operation: UUID, confirmed: RecoveryPlan) -> Result:
        if (operation, confirmed.receipt) not in await self.records.recoveries(plugin):
            raise OperationError(
                Error(
                    code=ErrorCode.CONFLICT,
                    message="No managed-file recovery is available for that operation.",
                )
            )
        current = await asyncio.to_thread(RecoveryPlan.inspect, confirmed.receipt)
        if current != confirmed or any(
            entry.status is RecoveryStep.CONFLICT for entry in current.entries
        ):
            return Result(
                outcome=Outcome.BLOCKED,
                message="A local change conflicts with the confirmed recovery.",
            )
        await self.records.reserve_restore(
            operation, confirmed.receipt, {str(entry.path) for entry in current.entries}
        )
        recipes = Path(__file__).resolve().parents[1] / "execution" / "recipes"
        try:
            raw = await pipe(
                make_command(
                    [
                        "-rRs",
                        "-f",
                        str(recipes / "environment.mk"),
                        "-f",
                        str(recipes / "recovery.mk"),
                        "--",
                        "restore-operation",
                    ]
                ),
                confirmed.model_dump_json().encode(),
                {**_environment(), "SETUP_SYSTEM_INTERPRETER": sys.executable},
                recipes,
                self.options.idle_timeout_seconds,
            )
            result = Result.model_validate_json(raw)
        except (OperationError, ValueError, OSError, TimeoutError, ExceptionGroup):
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Inspect the interrupted recovery before continuing.",
            )
        if result.outcome is Outcome.COMPLETE:
            await self.records.finish_restore(operation)
        return result
