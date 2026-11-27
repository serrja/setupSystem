"""Run a fixed, normal-user Make recipe against its confirmed plugin plan."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import anyio

from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import (
    ReceiptState,
    RecoveryStep,
    ResourceEffect,
    ResourceOwnership,
)
from setup_system_sdk.environment import session_environment
from setup_system_sdk.files.file_publication import observe
from setup_system_sdk.files.managed_files import apply_files
from setup_system_sdk.files.receipt_repository import (
    list_operations,
    load_operation,
    operation_folder,
)
from setup_system_sdk.files.receipts import FileState
from setup_system_sdk.files.recovery import restore
from setup_system_sdk.files.user_storage import checked_directory, inside_home
from setup_system_sdk.operations import Receipt, StepPlan
from setup_system_sdk.plugin import Backend, PluginSettings, owned_resources
from setup_system_sdk.processes import run_command
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result


def command(*argv: str, capture: bool = False) -> tuple[int, str]:
    """Bound a native tool probe and discard stderr, including account diagnostics."""
    if not Path(argv[0]).is_absolute():
        raise ValueError("A native tool requires an absolute executable path")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LC_ALL": "C.UTF-8"}
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    if os.geteuid() != 0:
        env.update(session_environment())
    result = run_command(
        list(argv),
        timeout=20,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        discard_output=not capture,
        discard_stderr=True,
        max_output_bytes=32768,
    )
    return result.returncode, result.stdout or ""


def single_file_chain(path: Path, state: FileState) -> list[str]:
    """Match exact single-file receipts back to the operation that created the file."""
    relative = path.relative_to(Path.home()).as_posix()
    chain: list[str] = []
    previous = state
    records = []
    for name, status in list_operations():
        if status is ReceiptState.COMPLETE:
            operation = load_operation(operation_folder(name))
            if (
                len(operation.files) == 1
                and not operation.requested_directories
                and operation.files[0].verified
                and operation.files[0].relative == relative
            ):
                records.append((name, operation.files[0]))
    while len(chain) < 128:
        matches = [
            (name, row) for name, row in records if row.after == previous and name not in chain
        ]
        if len(matches) != 1:
            break
        name, row = matches[0]
        chain.append(name)
        if row.before is None:
            return chain
        previous = row.before
    raise ValueError("No unambiguous recovery chain leads to creation of this file")


def owned_file_removal(path: Path) -> Plan:
    state = observe(path)
    if not state:
        return Plan(outcome=Outcome.NO_CHANGES, summary="The selected file is absent")
    own = next((r for r in owned_resources() if r.identity == str(path) and r.kind == "file"), None)
    if (
        not own
        or not own.cleanup
        or own.ownership != "created"
        or own.after != state.sha256
        or own.permissions != state.mode
    ):
        return Plan(outcome=Outcome.BLOCKED, summary="Review the unmanaged or modified file")
    chain = single_file_chain(path, state)
    return Plan(
        summary=f"Remove the verified owned file {path}",
        resources=[
            Resource(
                kind="file",
                identity=str(path),
                ownership=ResourceOwnership.CREATED,
                before=state.sha256,
                permissions=state.mode,
                cleanup=True,
                effect=ResourceEffect.REMOVE,
            )
        ],
        warnings=["Verified recovery receipts: " + ", ".join(chain)],
    )


def remove_owned_file(path: Path, plan: Plan) -> Result:
    state = observe(path)
    if not state or owned_file_removal(path) != plan or plan.outcome != Outcome.READY:
        return Result(outcome=Outcome.BLOCKED, message="The selected file changed")
    chain = single_file_chain(path, state)
    for name in chain:
        if any(entry.status is RecoveryStep.CONFLICT for entry in restore(name)):
            return Result(outcome=Outcome.PARTIAL, message="File recovery requires review")
        restore(name, apply=True)
    ok = observe(path) is None
    return Result(
        outcome=Outcome.CHANGED if ok else Outcome.PARTIAL,
        message="Inspected the selected file after recovery",
        resources=plan.resources,
    )


class DelegateActionError(Exception):
    pass


class NativeBackend(Backend):
    """Adapt synchronous local inspection and publication to the SDK worker."""

    executable: ClassVar[str]
    manual: ClassVar[Mapping[StrEnum, str]] = {}

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        present = await anyio.to_thread.run_sync(os.access, self.executable, os.X_OK)
        return Result(
            outcome=Outcome.NO_CHANGES,
            message="Program present" if present else "Program executable unavailable",
            resources=[
                Resource(
                    kind="executable",
                    identity=self.executable,
                    ownership=ResourceOwnership.PREEXISTING,
                )
            ]
            if present
            else [],
        )

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id in self.delegated_actions:
            raise DelegateActionError(action_id)
        if action_id == LifecycleAction.CHECK:
            present = os.access(self.executable, os.X_OK)
            return Plan(
                outcome=Outcome.NO_CHANGES if present else Outcome.BLOCKED,
                summary="Program present" if present else "Program executable unavailable",
                resources=[],
            )
        if action_id in self.manual:
            return Plan(
                outcome=Outcome.NEEDS_USER_ACTION,
                summary=self.manual[self.command(action_id).id],
                resources=[],
            )
        raise ValueError("Unknown action")

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        try:
            return await anyio.to_thread.run_sync(self.prepare, action_id, settings, inputs)
        except DelegateActionError:
            if context is None:
                raise ValueError("Planning requires the selected provider") from None
            return await self.delegated_plan(action_id, inputs, context)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        raise ValueError("No mutation is registered for this action")

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        return Result(outcome=Outcome.UNKNOWN_OUTCOME, message="Readback unavailable")

    def local_arguments(self, step: StepPlan) -> tuple[PluginSettings, Inputs, Plan]:
        settings = self.settings_model.model_validate(step.payload["settings"])
        inputs = self.command(step.handler).schema.model_validate(step.payload["inputs"])
        proposal = Plan.model_validate(step.preconditions["proposal"])
        if tuple(proposal.resources) != step.resources:
            raise ValueError("Step resources differ from the confirmed proposal")
        return settings, inputs, proposal

    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt:
        if context.native_call is not None:
            return await context.execute_native()
        settings, inputs, proposal = self.local_arguments(step)
        fresh = await anyio.to_thread.run_sync(self.prepare, step.handler, settings, inputs)
        if fresh != proposal:
            raise ValueError("The confirmed local precondition changed")
        if proposal.outcome is Outcome.NO_CHANGES:
            result = Result(
                outcome=Outcome.NO_CHANGES, message=proposal.summary, resources=proposal.resources
            )
        else:
            result = await anyio.to_thread.run_sync(
                self.mutate, step.handler, settings, inputs, proposal
            )
        return Receipt(
            operation_id=context.operation_id,
            step_key=step.step_key,
            executor=context.executor,
            result=result,
        )

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        settings, inputs, proposal = self.local_arguments(step)
        return await self.command(step.handler).readback(proposal, settings, inputs, context)


class FilesBackend(NativeBackend):
    """Review exact home-file bytes, preserve local edits and verify the desired state."""

    file_actions: ClassVar[frozenset[str]]

    def mode(self, path: Path) -> int:
        return 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        raise NotImplementedError

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id not in self.file_actions:
            return super().prepare(action_id, settings, inputs)
        resources = []
        changed = False
        for path, content in self.files(action_id, settings, inputs).items():
            inside_home(path)
            checked_directory(path.parent)
            state = observe(path)
            before = state.sha256 if state else None
            after = hashlib.sha256(content).hexdigest()
            changed |= before != after
            resources.append(
                Resource(
                    kind="file",
                    identity=str(path),
                    before=before,
                    after=after,
                    ownership=ResourceOwnership.CREATED
                    if state is None
                    else (
                        ResourceOwnership.PREEXISTING
                        if before == after
                        else ResourceOwnership.MODIFIED
                    ),
                    cleanup=state is None,
                    permissions=state.mode if state else self.mode(path),
                )
            )
        return Plan(
            summary=f"Review {len(resources)} file(s) for {action_id}",
            outcome=Outcome.READY if changed else Outcome.NO_CHANGES,
            resources=resources,
        )

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        desired = self.files(action_id, settings, inputs)
        if [(str(p), hashlib.sha256(b).hexdigest()) for p, b in desired.items()] != [
            (r.identity, r.after) for r in plan.resources
        ]:
            return Result(outcome=Outcome.BLOCKED, message="Desired file state changed")
        observed = {p: observe(p) for p in desired}
        if any(
            (state.sha256 if state else None) != row.before
            or (state.mode if state else self.mode(Path(row.identity))) != row.permissions
            for state, row in zip(observed.values(), plan.resources, strict=True)
        ):
            return Result(outcome=Outcome.BLOCKED, message="File state changed after confirmation")
        backup = apply_files(
            desired,
            expected=observed,
            modes={p: self.mode(p) for p, state in observed.items() if state is None},
        )
        return Result(
            outcome=Outcome.CHANGED,
            message="Files published",
            resources=plan.resources,
            recovery_id=backup.name,
        )

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        def matches(row: Resource) -> bool:
            if row.kind != "file":
                return False
            state = observe(inside_home(Path(row.identity)))
            return bool(state and state.sha256 == row.after and state.mode == row.permissions)

        verified = bool(plan.resources) and await anyio.to_thread.run_sync(
            lambda: all(matches(r) for r in plan.resources)
        )
        return Result.observed(
            outcome=Outcome.COMPLETE if verified else Outcome.PARTIAL,
            message="File readback verified" if verified else "File readback differs",
            resources=plan.resources,
        )
