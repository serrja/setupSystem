"""Manage the current login's Zsh fragments and preserve shell history."""

import asyncio
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_plugin_zsh.runtime import (
    _snapshot,
    apply_shell,
    history_directory,
    inspect_shell,
    validate_shell,
)
from setup_system.domain.receipts import digest
from setup_system.infrastructure.file_publication import observe
from setup_system_sdk import (
    EnvironmentValue,
    Inputs,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
)
from setup_system_sdk.apt import DebianPackages, apply_packages


class Terminal(EnvironmentValue):
    name: Literal["TERM"] = "TERM"
    value: Literal["xterm-256color", "xterm", "dumb"] = "xterm-256color"


class Startup(Inputs):
    flags: tuple[Literal["-c", "-ic", "-lic"], ...] = Field(
        default=("-c", "-ic", "-lic"),
        min_length=1,
        max_length=3,
        description="Native startup modes to verify in an isolated home.",
    )
    environment: tuple[Terminal, ...] = Field(
        default=(), max_length=1, description="Terminal settings for this check."
    )


class Plugin(DebianPackages):
    package_folder = Path(__file__).parent
    executable = "/usr/bin/zsh"
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "configure": Inputs,
        "verify-startup": Startup,
    }

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == "verify-startup" and isinstance(inputs, Startup):
            status = inspect_shell()
            if not status.zsh:
                return Plan(outcome=Outcome.BLOCKED, summary="Install Zsh first")
            terminal = inputs.environment[0].value if inputs.environment else "xterm-256color"
            validate_shell(self.package_folder, status.zsh, inputs.flags, terminal)
            return Plan(
                outcome=Outcome.NO_CHANGES, summary="Verified the selected native Zsh startup modes"
            )
        if action_id != "configure":
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
                    ownership="modified" if original else "created",
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
                ownership="preexisting" if existing else "created",
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
        if action_id != "configure":
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

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
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

        ok = await asyncio.to_thread(matches)
        return Result(
            outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
            message="Shell fragment readback complete" if ok else "Shell fragments differ",
            resources=plan.resources if ok else [],
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("zsh", action_id, plan)
