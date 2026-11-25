"""Bind reviewed plugin releases, plans, resource reservations and native readback."""

from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import sys
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID, uuid4

from platformdirs import user_data_path, user_state_path
from pydantic import Field, TypeAdapter

from setup_system.application.rollback import RecoveryPlan
from setup_system.authorization import BecomePassword, _environment, session_environment, ssh_socket
from setup_system.configuration import ApplicationOptions, Settings
from setup_system.extension_lifecycle import generation, generation_lock
from setup_system.extensions import Activation, active, builtins
from setup_system.infrastructure.encryption import validate_home_access
from setup_system.infrastructure.operation_io import read_wire
from setup_system.journal import Event, Journal
from setup_system.layout import INSTALLED, verify_installation
from setup_system.make_transport import Phase, authenticated_make
from setup_system.resource_ledger import LocalSnapshot, ResourceLedger
from setup_system_sdk import Action, Inputs, Mutation, Outcome, Plan, Result, Values
from setup_system_sdk.protocol import LIMIT, Reply, Request

_cancellation: ContextVar[asyncio.Event | None] = ContextVar("plugin_cancellation", default=None)


class Snapshot(Inputs):
    operation_id: UUID = Field(default_factory=uuid4)
    digest: str
    plugin_id: str
    mutation: Mutation = Field(repr=False)


async def _pipe(
    argv: list[str], payload: bytes, env: dict[str, str], cwd: Path, timeout: float | None
) -> bytes:
    if len(payload) > LIMIT:
        raise ValueError("Request exceeds the protocol size limit")
    child = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    assert child.stdin and child.stdout

    async def send() -> None:
        assert child.stdin
        try:
            child.stdin.write(payload)
            await child.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            child.stdin.close()

    async def receive() -> bytes:
        assert child.stdout
        chunks = bytearray()
        overflow = False
        while chunk := await child.stdout.read(16384):
            if len(chunks) + len(chunk) <= LIMIT and not overflow:
                chunks.extend(chunk)
            else:
                overflow = True
        if await child.wait() or overflow:
            raise ValueError("The plugin returned no usable response")
        return bytes(chunks)

    try:
        async with asyncio.timeout(timeout), asyncio.TaskGroup() as group:
            group.create_task(send())
            reply = group.create_task(receive())
        return reply.result()
    finally:
        if child.returncode is None:
            if timeout is not None:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
            await child.wait()


def worker_environment(state: Activation, action_id: str | None) -> dict[str, str]:
    env = session_environment()
    if action_id and "account-keys" in descriptor(state, action_id).permissions:
        env.update(ssh_socket())
    return env


async def query(state: Activation, request: Request, options: ApplicationOptions) -> Reply:
    if state.manifest.id != request.plugin_id:
        raise ValueError("Incorrect worker identity")
    with generation_lock(generation(state), exclusive=False):
        raw = await _pipe(
            [str(state.python), "-I", "-B", "-m", "setup_system_sdk.worker"],
            request.model_dump_json().encode(),
            {
                **worker_environment(state, request.action_id),
                **{k: os.environ[k] for k in state.manifest.environment_fields if k in os.environ},
            },
            state.resources,
            options.read_only_timeout_seconds,
        )
    return Reply.model_validate_json(raw)


def descriptor(state: Activation, action_id: str) -> Action:
    for action in state.manifest.actions:
        if action.id == action_id:
            return action
    raise ValueError("The installed plugin does not declare this action")


def protected_resources(state: Activation) -> Path:
    verify_installation(INSTALLED)
    item = next(
        (
            entry
            for entry in builtins(INSTALLED)
            if entry.manifest == state.manifest
            and entry.release
            and entry.release.digest == state.digest
        ),
        None,
    )
    if item is None:
        raise ValueError("Administrative actions require the exact Debian-installed plugin release")
    root = INSTALLED / "plugins" / state.manifest.id / state.manifest.entry_point.split(":")[0]
    if not all(p.is_file() for p in (INSTALLED / "sdk/environment.mk", root / "Makefile")):
        raise ValueError("The protected Make resources are incomplete")
    return root


class AsyncMakeRunner:
    def __init__(self, options: ApplicationOptions) -> None:
        self.options = options

    async def execute(
        self, state: Activation, snapshot: Snapshot, password: BecomePassword | None
    ) -> Result:
        action = descriptor(state, snapshot.mutation.action_id)
        if not action.target:
            raise ValueError("This action has no mutation target")
        privileged = bool({"root", "sudo", "su"} & set(action.permissions))
        if privileged != (password is not None):
            raise ValueError("The action requires a different authorization context")
        root = (
            await asyncio.to_thread(protected_resources, state) if privileged else state.resources
        )
        env = (
            _environment()
            if privileged
            else await asyncio.to_thread(worker_environment, state, action.id)
        )
        env["SETUP_SYSTEM_INTERPRETER"] = "/usr/bin/python3" if privileged else str(state.python)
        makefiles = [
            str(
                INSTALLED / "sdk/environment.mk"
                if privileged
                else Path(__file__).parents[1] / "setup_system_sdk/environment.mk"
            ),
            str(root / "Makefile"),
        ]
        args = [
            "-rR",
            "-s",
            "-j1",
            "--no-print-directory",
            "-f",
            makefiles[0],
            "-f",
            makefiles[1],
            "--",
            action.target,
        ]
        wire = snapshot.mutation.model_dump_json().encode()
        if not privileged:
            await asyncio.to_thread(validate_home_access, Path.home())
            raw = await _pipe(
                ["/usr/bin/make", *args], wire, env, root, self.options.idle_timeout_seconds
            )
            return Result.model_validate_json(raw)
        assert password
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / "request.json"
            result = request.with_name("result.json")

            def stage() -> None:
                with os.fdopen(
                    os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as stream:
                    stream.write(wire)

            await asyncio.to_thread(stage)
            env.update(
                SETUP_SYSTEM_REQUEST=str(request),
                SETUP_SYSTEM_RESULT=str(result),
                SETUP_SYSTEM_CANCEL=str(request.with_name("cancel")),
            )
            started = False

            def phase(value: Phase) -> None:
                nonlocal started
                if value is Phase.RUNNING:
                    started = True

            code = await authenticated_make(
                root,
                args,
                env,
                _cancellation.get() or asyncio.Event(),
                phase,
                password.take,
                options=self.options,
                root_account="su" in action.permissions,
            )
            if not result.exists() and not started:
                return Result(
                    outcome=Outcome.BLOCKED, message="Authorization or preflight did not complete"
                )
            native = Result.model_validate_json(
                await asyncio.to_thread(read_wire, result, limit=LIMIT, private=True)
            )
            if code and native.outcome in (Outcome.CHANGED, Outcome.COMPLETE, Outcome.NO_CHANGES):
                raise ValueError("Native transaction and result disagree")
            return native


class PluginManager:
    def __init__(
        self,
        env_root: Path | None = None,
        state_root: Path | None = None,
        options: ApplicationOptions | None = None,
    ) -> None:
        self.env_root = env_root or user_data_path("setup-system") / "extensions"
        directory = state_root or user_state_path("setup-system")
        self.ledger = ResourceLedger(directory / "resources.sqlite3")
        self.journal = Journal(directory / "history")
        self.options = options or Settings.application()
        self.runner = AsyncMakeRunner(self.options)
        self._running: dict[UUID, asyncio.Task[Result]] = {}
        self._tokens: dict[UUID, tuple[str, asyncio.Event]] = {}

    async def activation(self, plugin_id: str) -> Activation:
        state = await asyncio.to_thread(active, self.env_root, plugin_id)
        if state is None:
            raise ValueError("Activate a trusted extension first")
        return state

    async def preview(
        self,
        plugin_id: str,
        action_id: str,
        settings: Values,
        inputs: Values,
        *,
        profile: Values | None = None,
        saved: Values | None = None,
    ) -> Snapshot:
        state = await self.activation(plugin_id)
        descriptor(state, action_id)
        dependents = await self.ledger.dependents(plugin_id) if action_id == "uninstall" else []
        holdings = tuple(await self.ledger.holdings(plugin_id))
        request = Request(
            holdings=holdings,
            plugin_id=plugin_id,
            method="plan",
            action_id=action_id,
            overrides=settings,
            saved=saved or {},
            profile=profile or {},
            inputs=inputs,
        )
        reply = await query(state, request, self.options)
        if reply.plan is None:
            raise ValueError("Backend did not return a plan")
        return Snapshot(
            plugin_id=plugin_id,
            digest=state.digest,
            mutation=Mutation(
                holdings=holdings,
                action_id=action_id,
                inputs=inputs,
                settings=reply.settings,
                plan=Plan(
                    summary="Required by installed programs: " + ", ".join(dependents),
                    outcome=Outcome.BLOCKED,
                )
                if dependents
                else reply.plan,
            ),
        )

    async def apply(self, snapshot: Snapshot, password: BecomePassword | None = None) -> Result:
        snapshot = Snapshot.model_validate_json(snapshot.model_dump_json())
        key = snapshot.operation_id
        if key in self._running:
            if password:
                password.discard()
            return await asyncio.shield(self._running[key])

        cancel = asyncio.Event()
        self._tokens[key] = (snapshot.plugin_id, cancel)

        async def perform() -> Result:
            context = _cancellation.set(cancel)
            try:
                async with self.ledger.writer(snapshot.plugin_id):
                    return await self._apply(snapshot, password)
            finally:
                if password:
                    password.discard()
                self._running.pop(key, None)
                self._tokens.pop(key, None)
                _cancellation.reset(context)

        task = asyncio.create_task(perform())
        self._running[key] = task
        return await asyncio.shield(task)

    def cancel(self, plugin: str) -> int:
        pending = [event for owner, event in self._tokens.values() if owner == plugin]
        for event in pending:
            event.set()
        return len(pending)

    async def drain(self) -> None:
        if self._running:
            await asyncio.gather(*tuple(self._running.values()), return_exceptions=True)

    async def _apply(self, snapshot: Snapshot, password: BecomePassword | None) -> Result:
        state = await self.activation(snapshot.plugin_id)
        with generation_lock(generation(state), exclusive=False):
            return await self._apply_in_generation(state, snapshot, password)

    async def _apply_in_generation(
        self, state: Activation, snapshot: Snapshot, password: BecomePassword | None
    ) -> Result:
        item = snapshot.mutation
        action = descriptor(state, item.action_id)

        def event(phase: str, outcome: Outcome | None = None) -> Event:
            return Event.model_validate(
                dict(
                    plugin_id=snapshot.plugin_id,
                    operation_id=snapshot.operation_id,
                    action_id=item.action_id,
                    phase=phase,
                    message_code="operation_" + phase,
                    outcome=outcome,
                    details={"resource_count": len(item.plan.resources)},
                )
            )

        track_only = (
            item.plan.outcome is Outcome.NO_CHANGES
            and action.id in ("install", "update")
            and all(
                r.effect == "ensure"
                and r.before == r.after
                and r.ownership in ("preexisting", "shared")
                for r in item.plan.resources
            )
        )
        if (
            (item.plan.outcome != Outcome.READY and not track_only)
            or not action.target
            or snapshot.digest != state.digest
        ):
            return Result(outcome=Outcome.BLOCKED, message="This snapshot cannot be applied")
        if not track_only and {"root", "sudo", "su"} & set(action.permissions):
            await asyncio.to_thread(protected_resources, state)
            if password is None:
                return Result(outcome=Outcome.BLOCKED, message="Native authorization is required")
        holdings = tuple(await self.ledger.holdings(snapshot.plugin_id))
        if holdings != item.holdings:
            return Result(outcome=Outcome.BLOCKED, message="Ownership changed. Preview again.")
        request = Request(
            holdings=holdings,
            plugin_id=snapshot.plugin_id,
            method="plan",
            action_id=item.action_id,
            overrides=item.settings,
            inputs=item.inputs,
        )
        fresh = await query(state, request, self.options)
        if fresh.plan != item.plan or fresh.settings != item.settings:
            return Result(
                outcome=Outcome.BLOCKED, message="Facts changed. Preview the action again."
            )
        try:
            await self.ledger.prepare(
                snapshot.operation_id,
                snapshot.plugin_id,
                item.action_id,
                state.digest,
                item.plan,
                snapshot=snapshot.model_dump_json(),
                dependencies=tuple(state.manifest.dependencies) if not action.cleanup_phase else (),
                remove_preexisting=(
                    "root" in action.permissions and "remove-preexisting" in action.permissions
                )
                or (
                    "remove-selected" in action.permissions
                    and action.cleanup_phase is None
                    and len(item.plan.resources) == 1
                    and item.plan.resources[0].effect == "remove"
                    and bool(item.plan.resources[0].before)
                ),
            )
        except (ValueError, sqlite3.IntegrityError):
            return Result(
                outcome=Outcome.BLOCKED,
                message="Operation already used or resource ownership conflicts",
            )
        await self.journal.emit(event("prepared"))
        if track_only:
            final = Result(
                outcome=Outcome.NO_CHANGES,
                message="Verified existing program and recorded its dependencies",
                resources=item.plan.resources,
            )
            await self.ledger.finish(snapshot.operation_id, final)
            await self.journal.emit(event("verified", final.outcome))
            return final
        try:
            token = _cancellation.get()
            native = (
                Result(outcome=Outcome.CANCELLED, message="Cancelled before native changes")
                if token and token.is_set()
                else await self.runner.execute(state, snapshot, password)
            )
            if native.outcome in (
                Outcome.CHANGED,
                Outcome.COMPLETE,
                Outcome.NEEDS_REBOOT,
                Outcome.NO_CHANGES,
            ):
                await self.ledger.applied(snapshot.operation_id, native.recovery_id)
                await self.journal.emit(event("applied"))
                authenticated_readback = (
                    password is not None
                    and bool({"root", "sudo", "su"} & set(action.permissions))
                    and native.resources
                    and all(r.verified for r in native.resources)
                    and {(r.kind, r.identity) for r in native.resources}
                    == {(r.kind, r.identity) for r in item.plan.resources}
                )
                if authenticated_readback:
                    final = native
                else:
                    reply = await query(
                        state,
                        request.model_copy(update={"method": "verify", "plan": item.plan}),
                        self.options,
                    )
                    if reply.result is None or reply.result.outcome == Outcome.READY:
                        raise ValueError("Missing verification")
                    final = reply.result.model_copy(update={"recovery_id": native.recovery_id})
            else:
                final = native
            await self.ledger.finish(snapshot.operation_id, final)
        except Exception:
            final = Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Inspect current state before continuing. The native outcome is uncertain.",
            )
            await self.ledger.finish(snapshot.operation_id, final)
        unresolved = final.outcome in (Outcome.UNKNOWN_OUTCOME, Outcome.PARTIAL)
        await self.journal.emit(event("unresolved" if unresolved else "verified", final.outcome))
        return final

    async def reconcile(
        self,
        plugin: str,
        authorize: Callable[[Action, Snapshot], Awaitable[BecomePassword | None]] | None = None,
    ) -> list[Result]:
        async with self.ledger.writer(plugin):
            return await self._reconcile(plugin, authorize)

    async def _reconcile(
        self,
        plugin: str,
        authorize: Callable[[Action, Snapshot], Awaitable[BecomePassword | None]] | None,
    ) -> list[Result]:
        results = []
        for wire in await self.ledger.pending(plugin):
            snapshot = TypeAdapter(LocalSnapshot | Snapshot).validate_json(wire)
            if isinstance(snapshot, LocalSnapshot):
                from setup_system.cleanup import reconcile_local

                results.append(await reconcile_local(self, plugin, snapshot))
                continue
            if snapshot.operation_id in self._running:
                continue
            state = await self.activation(plugin)
            if state.digest != snapshot.digest:
                raise ValueError("Reconciliation requires the previously approved release")
            item = snapshot.mutation
            reply = await query(
                state,
                Request(
                    method="verify",
                    plugin_id=plugin,
                    action_id=item.action_id,
                    overrides=item.settings,
                    inputs=item.inputs,
                    plan=item.plan,
                    holdings=item.holdings,
                ),
                self.options,
            )
            result = reply.result
            action = descriptor(state, item.action_id)
            if (
                result is not None
                and result.outcome is Outcome.NEEDS_USER_ACTION
                and action.read_only
                and {"sudo", "su", "root"}.intersection(action.permissions)
                and authorize
            ):
                await asyncio.to_thread(protected_resources, state)
                password = await authorize(action, snapshot)
                if password:
                    try:
                        with generation_lock(generation(state), exclusive=False):
                            verified = await self.runner.execute(state, snapshot, password)
                        if (
                            verified.resources
                            and all(r.verified for r in verified.resources)
                            and {(r.kind, r.identity) for r in verified.resources}
                            == {(r.kind, r.identity) for r in item.plan.resources}
                        ):
                            result = verified
                    finally:
                        password.discard()
            if result is not None and result.outcome is Outcome.NEEDS_USER_ACTION:
                results.append(result)
                continue
            if result is None or result.outcome not in (
                Outcome.COMPLETE,
                Outcome.CHANGED,
                Outcome.NO_CHANGES,
                Outcome.NEEDS_REBOOT,
            ):
                results.append(
                    Result(outcome=Outcome.PARTIAL, message="Native state still needs review")
                )
                continue
            await self.ledger.finish(snapshot.operation_id, result)
            results.append(result)
        return results

    async def remaining_cleanup(self, plugin: str) -> list[Snapshot]:
        """Preview only the still-present, originally approved removal targets."""
        state = await self.activation(plugin)
        proposals = []
        for wire in await self.ledger.pending(plugin):
            original = TypeAdapter(Snapshot | LocalSnapshot).validate_json(wire)
            if isinstance(original, LocalSnapshot) or original.digest != state.digest:
                continue
            item = original.mutation
            if (
                not descriptor(state, item.action_id).cleanup_phase
                or not item.plan.resources
                or any(
                    r.effect != "remove" or r.ownership != "created" or not r.cleanup
                    for r in item.plan.resources
                )
            ):
                continue
            current = await self.preview(plugin, item.action_id, item.settings, item.inputs)
            if (
                current.mutation.plan.outcome != Outcome.READY
                or not current.mutation.plan.resources
            ):
                continue
            left = current.mutation.plan.resources
            if any(row not in item.plan.resources for row in left):
                continue
            removed = [r for r in item.plan.resources if r not in left]
            if removed:
                probe = await query(
                    state,
                    Request(
                        method="verify",
                        plugin_id=plugin,
                        action_id=item.action_id,
                        overrides=item.settings,
                        inputs=item.inputs,
                        holdings=item.holdings,
                        plan=item.plan.model_copy(update={"resources": removed}),
                    ),
                    self.options,
                )
                if (
                    not probe.result
                    or probe.result.outcome not in (Outcome.COMPLETE, Outcome.NO_CHANGES)
                    or {(r.kind, r.identity) for r in probe.result.resources}
                    != {(r.kind, r.identity) for r in removed}
                ):
                    continue
            proposals.append(current.model_copy(update={"operation_id": original.operation_id}))
        return proposals

    async def resume_cleanup(
        self, snapshot: Snapshot, password: BecomePassword | None = None
    ) -> Result:
        """Resume a newly confirmed removal, retaining its original receipts and reservations."""
        try:
            async with self.ledger.writer(snapshot.plugin_id):
                if snapshot not in await self.remaining_cleanup(snapshot.plugin_id):
                    return Result(
                        outcome=Outcome.BLOCKED, message="Cleanup changed after confirmation"
                    )
                state = await self.activation(snapshot.plugin_id)
                with generation_lock(generation(state), exclusive=False):
                    try:
                        result = await self.runner.execute(state, snapshot, password)
                    except (OSError, ValueError, TimeoutError, ExceptionGroup):
                        return Result(
                            outcome=Outcome.UNKNOWN_OUTCOME, message="Inspect interrupted cleanup"
                        )
                if result.outcome in (Outcome.COMPLETE, Outcome.CHANGED, Outcome.NO_CHANGES):
                    verified = await self._reconcile(snapshot.plugin_id, None)
                    if not await self.ledger.pending(snapshot.plugin_id) and all(
                        r.outcome in (Outcome.COMPLETE, Outcome.CHANGED, Outcome.NO_CHANGES)
                        for r in verified
                    ):
                        return Result(
                            outcome=Outcome.COMPLETE, message="Verified the entire original removal"
                        )
                return Result(
                    outcome=Outcome.PARTIAL, message="Inspect remaining cleanup resources"
                )
        finally:
            if password:
                password.discard()

    async def rollback(self, plugin: str, operation: str, confirmed: RecoveryPlan) -> Result:
        async with self.ledger.writer(plugin):
            return await self._rollback(plugin, operation, confirmed)

    async def _rollback(self, plugin: str, operation: str, confirmed: RecoveryPlan) -> Result:
        if (operation, confirmed.receipt) not in await self.ledger.recoveries(plugin):
            raise ValueError("No managed-file recovery is available for this operation")
        current = await asyncio.to_thread(RecoveryPlan.inspect, confirmed.receipt)
        if current != confirmed or any(s == "conflict" for _, s in current.entries):
            return Result(
                outcome=Outcome.BLOCKED, message="A later change conflicts with this recovery"
            )
        try:
            await self.ledger.reserve_rollback(
                plugin, operation, confirmed.receipt, {name for name, _ in current.entries}
            )
        except (ValueError, sqlite3.IntegrityError):
            return Result(
                outcome=Outcome.BLOCKED,
                message="A newer operation or another consumer holds these resources",
            )
        result = await self.run_recovery(confirmed)
        if result.outcome is Outcome.COMPLETE:
            await self.ledger.finish_rollback(plugin, operation)
        await self.journal.emit(
            Event(
                plugin_id=plugin,
                operation_id=UUID(operation),
                action_id="rollback",
                phase="verified" if result.outcome is Outcome.COMPLETE else "unresolved",
                message_code="operation_rollback",
                outcome=result.outcome,
            )
        )
        return result

    async def run_recovery(self, confirmed: RecoveryPlan) -> Result:
        sdk = Path(__file__).resolve().parents[1] / "setup_system_sdk"
        env = _environment()
        env["SETUP_SYSTEM_INTERPRETER"] = sys.executable
        try:
            raw = await _pipe(
                [
                    "/usr/bin/make",
                    "-rRs",
                    "-f",
                    str(sdk / "environment.mk"),
                    "-f",
                    str(sdk / "recovery.mk"),
                    "--",
                    "restore-operation",
                ],
                confirmed.model_dump_json().encode(),
                env,
                sdk,
                self.options.idle_timeout_seconds,
            )
            return Result.model_validate_json(raw)
        except (OSError, ValueError, ExceptionGroup, TimeoutError):
            return Result(
                outcome=Outcome.UNKNOWN_OUTCOME,
                message="Inspect the recovery receipt before continuing",
            )
