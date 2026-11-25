"""Run a fixed, normal-user Make recipe against its confirmed plugin plan."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from platformdirs import user_data_dir
from pydantic import Field, field_validator

from setup_system.application.recovery import restore
from setup_system.authorization import session_environment
from setup_system.diagnostics import PartialApplyError
from setup_system.domain.receipts import FileState
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.receipt_repository import (
    list_operations,
    load_operation,
    operation_folder,
)
from setup_system.infrastructure.user_storage import checked_directory, inside_home
from setup_system_sdk import (
    Backend,
    Configurable,
    Inputs,
    Installable,
    Mutation,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
    Uninstallable,
    owned_resources,
    ownership_context,
)

ActionRequest = Mutation


def command(*argv: str, capture: bool = False) -> tuple[int, str]:
    """Bound a native tool probe and discard stderr, including account diagnostics."""
    if not Path(argv[0]).is_absolute():
        raise ValueError("A native tool requires an absolute executable path")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LC_ALL": "C.UTF-8"}
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    if os.geteuid() != 0:
        session = session_environment()
        env.update(
            {
                name: session[name]
                for name in ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "DISPLAY")
                if name in session
            }
        )
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
        if status == "complete":
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
                ownership="created",
                before=state.sha256,
                permissions=state.mode,
                cleanup=True,
                effect="remove",
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
        if any(s == "conflict" for _, s in restore(name)):
            return Result(outcome=Outcome.PARTIAL, message="File recovery requires review")
        restore(name, apply=True)
    ok = observe(path) is None
    return Result(
        outcome=Outcome.CHANGED if ok else Outcome.PARTIAL,
        message="Inspected the selected file after recovery",
        resources=plan.resources,
    )


class NativeBackend(Backend):
    """Adapt synchronous local inspection and publication to the SDK worker."""

    executable: ClassVar[str]
    manual: ClassVar[Mapping[str, str]] = {}

    async def inspect(self, settings: PluginSettings) -> Result:
        present = await asyncio.to_thread(os.access, self.executable, os.X_OK)
        return Result(
            outcome=Outcome.NO_CHANGES,
            message="Program present" if present else "Program executable unavailable",
            resources=[
                Resource(kind="executable", identity=self.executable, ownership="preexisting")
            ]
            if present
            else [],
        )

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == "check":
            present = os.access(self.executable, os.X_OK)
            return Plan(
                outcome=Outcome.NO_CHANGES if present else Outcome.BLOCKED,
                summary="Program present" if present else "Program executable unavailable",
                resources=[],
            )
        if action_id in self.manual:
            return Plan(
                outcome=Outcome.NEEDS_USER_ACTION, summary=self.manual[action_id], resources=[]
            )
        raise ValueError("Unknown action")

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        return await asyncio.to_thread(self.prepare, action_id, settings, inputs)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        raise ValueError("No mutation is registered for this action")

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        return Result(outcome=Outcome.UNKNOWN_OUTCOME, message="Readback unavailable")

    async def apply_request(self, target: str, request: ActionRequest) -> Result:
        if os.geteuid() == 0 or request.action_id != target or target not in self.inputs:
            return Result(outcome=Outcome.BLOCKED, message="Unexpected execution context")
        settings = self.settings_model.model_validate(request.settings)
        inputs = self.inputs[target].model_validate(request.inputs)
        with ownership_context(request.holdings):
            observed = await self.plan(target, settings, inputs)
            if observed != request.plan or observed.outcome != Outcome.READY:
                return Result(outcome=Outcome.BLOCKED, message="Plan changed. Preview again.")
            if target == "install" and isinstance(self, Installable):
                return await self.install(settings, inputs, observed)
            if target == "configure" and isinstance(self, Configurable):
                return await self.configure(settings, inputs, observed)
            if target == "uninstall" and isinstance(self, Uninstallable):
                return await self.uninstall(settings, inputs, observed)
            return await asyncio.to_thread(self.mutate, target, settings, inputs, request.plan)


def recipe(backend: NativeBackend, target: str) -> None:
    """Consume one bounded request. Native output never enters the worker protocol."""
    try:
        data = sys.stdin.buffer.read(262145)
        if len(data) > 262144:
            raise ValueError("Oversize input")
        request = ActionRequest.model_validate_json(data)
    except ValueError:
        result = Result(outcome=Outcome.BLOCKED, message="Invalid action request")
    else:
        try:
            result = asyncio.run(backend.apply_request(target, request))
        except PartialApplyError:
            result = Result(outcome=Outcome.PARTIAL, message="Inspect managed-file recovery")
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
            result = Result(
                outcome=Outcome.UNKNOWN_OUTCOME, message="Check operation state before retry"
            )
    print(result.model_dump_json())


class LauncherProfile(Inputs):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$", title="Launcher profile ID")
    name: str = Field(min_length=1, max_length=80, title="Application menu label")

    @field_validator("name")
    @classmethod
    def menu_label(cls, value: str) -> str:
        if not value.isprintable() or value.strip() != value or "\\" in value:
            raise ValueError(
                "Enter a single-line menu label without surrounding whitespace or escapes"
            )
        return value


def launcher_file(
    profile: LauncherProfile, plugin: str, executable: str, args: tuple[str, ...]
) -> dict[Path, bytes]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", plugin) or not re.fullmatch(
        r"/usr/bin/[a-z][a-z0-9-]*", executable
    ):
        raise ValueError("Expected a packaged application and launcher namespace")
    if not all(re.fullmatch(r"-{1,2}[a-z][a-z-]*", arg) for arg in args):
        raise ValueError("The application launcher accepts only fixed native options")
    path = inside_home(
        Path(user_data_dir())
        / "applications"
        / f"setup-system-{plugin}-{profile.profile_id}.desktop"
    )
    content = (
        "[Desktop Entry]\nType=Application\n"
        f"Name={profile.name}\nTryExec={executable}\n"
        f"Exec={' '.join((executable, *args))}\n"
        f"Icon={Path(executable).name}\nTerminal=false\n"
    )
    return {path: content.encode()}


class FilesBackend(NativeBackend):
    """Review exact home-file bytes, preserve local edits and verify the desired state."""

    file_actions: ClassVar[frozenset[str]]

    async def configure(self, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result:
        return await asyncio.to_thread(self.mutate, "configure", settings, inputs, plan)

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
                    ownership="created"
                    if state is None
                    else ("preexisting" if before == after else "modified"),
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

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        def matches(row: Resource) -> bool:
            if row.kind != "file":
                return False
            state = observe(inside_home(Path(row.identity)))
            return bool(state and state.sha256 == row.after and state.mode == row.permissions)

        verified = bool(plan.resources) and await asyncio.to_thread(
            lambda: all(matches(r) for r in plan.resources)
        )
        return Result(
            outcome=Outcome.COMPLETE if verified else Outcome.PARTIAL,
            message="File readback verified" if verified else "File readback differs",
            resources=plan.resources,
        )
