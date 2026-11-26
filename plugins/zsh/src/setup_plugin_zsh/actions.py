"""Manage the current login's Zsh fragments and preserve shell history."""

from importlib.resources import files as package_files
from pathlib import Path

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.backend import NativeBackend
from setup_system_sdk.files.file_publication import observe
from setup_system_sdk.files.receipts import digest
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_zsh.enums import PluginAction
from setup_plugin_zsh.models import Startup
from setup_plugin_zsh.runtime import (
    _snapshot,
    apply_shell,
    history_directory,
    inspect_shell,
    validate_shell,
)


class PluginActions(NativeBackend):
    package_folder = Path(str(package_files("setup_plugin_zsh").joinpath("resources")))

    executable = "/usr/bin/zsh"

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == PluginAction.VERIFY_STARTUP and isinstance(inputs, Startup):
            status = inspect_shell()
            if not status.zsh:
                return Plan(outcome=Outcome.BLOCKED, summary="Install Zsh first")
            terminal = inputs.environment[0].value if inputs.environment else "xterm-256color"
            validate_shell(self.package_folder, status.zsh, inputs.flags, terminal)
            return Plan(
                outcome=Outcome.NO_CHANGES, summary="Verified the selected native Zsh startup modes"
            )
        if action_id != LifecycleAction.CONFIGURE:
            return super().prepare(action_id, settings, inputs)
        status = inspect_shell()
        if status.zsh is None:
            return Plan(outcome=Outcome.BLOCKED, summary="Install Zsh first")
        desired, _, changes = _snapshot(self.package_folder, status)
        rows = []
        for path, data in desired.items():
            original = observe(path)
            rows.append(
                Resource(
                    kind="file",
                    identity=str(path),
                    ownership=ResourceOwnership.MODIFIED if original else ResourceOwnership.CREATED,
                    before=original.sha256 if original else None,
                    before_permissions=original.mode if original else None,
                    after=digest(data),
                    permissions=original.mode if original else 0o600,
                    cleanup=original is None,
                )
            )
        history = history_directory(status)
        existing = history.exists()
        rows.append(
            Resource(
                kind="directory",
                identity=str(history),
                ownership=ResourceOwnership.PREEXISTING if existing else ResourceOwnership.CREATED,
                before="present" if existing else None,
                after="present",
                cleanup=False,
            )
        )
        return Plan(
            summary="Configure Zsh startup fragments. Your login shell and history are preserved.",
            resources=rows,
            outcome=Outcome.READY if changes or not existing else Outcome.NO_CHANGES,
        )

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id != LifecycleAction.CONFIGURE:
            raise ValueError("Unknown user action")
        files = [r for r in plan.resources if r.kind == "file"]
        directory = next(r for r in plan.resources if r.kind == "directory")
        if Path(directory.identity).exists() != (directory.before == "present"):
            return Result(
                outcome=Outcome.BLOCKED, message="History directory changed after preview"
            )
        current = {Path(r.identity): observe(Path(r.identity)) for r in files}
        if any(
            (state.sha256 if state else None) != r.before
            or (state.mode if state else None) != r.before_permissions
            for r, state in zip(files, current.values(), strict=True)
        ):
            return Result(outcome=Outcome.BLOCKED, message="Shell startup files changed")
        backup, _, _ = apply_shell(self.package_folder, reviewed=current)
        return Result(
            outcome=Outcome.CHANGED,
            message="Zsh configuration published",
            recovery_id=backup.name,
            resources=plan.resources,
        )

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if not plan.resources or plan.resources[0].kind != "file":
            return await super().verify(plan, settings, inputs)

        def matches() -> bool:
            return all(
                (state := observe(Path(r.identity)))
                and state.sha256 == r.after
                and state.mode == r.permissions
                for r in plan.resources
                if r.kind == "file"
            ) and all(
                Path(r.identity).is_dir() and not Path(r.identity).is_symlink()
                for r in plan.resources
                if r.kind == "directory"
            )

        ok = await run_sync(matches)
        return Result.observed(
            outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
            message="Shell fragment readback complete" if ok else "Shell fragments differ",
            resources=plan.resources if ok else [],
        )
