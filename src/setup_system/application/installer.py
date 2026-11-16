"""One installation use case shared by Typer and the dependency-free source handoff."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from setup_system import installation as system
from setup_system.diagnostics import error_text
from setup_system.runtime import RUNTIME_PYTHON, UV_VERSION, UserRuntime


class Operation(StrEnum):
    PLAN = "plan"
    APPLY = "apply"
    USER_STAGE = "user-stage"


@dataclass(frozen=True)
class InstallRequest:
    operation: Operation
    user: str | None = None
    yes: bool = False
    overrides: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class InstallPlan:
    target: system.TargetUser
    environment: dict[str, str]
    packages: tuple[str, ...]


class RuntimePort(Protocol):
    def lock(self) -> str | None: ...
    def inputs(self) -> list[tuple[Path, bool]]: ...
    def prepare(self) -> Path: ...


class UnsupportedTargetError(ValueError):
    """The selected machine cannot execute this installation profile."""


class InstallationService:
    """Own validation, consent and ordered system/user stages without a CLI parser."""

    def __init__(self, root: Path, *, runtime: RuntimePort | None = None) -> None:
        self.root = root.resolve()
        self.runtime = UserRuntime(self.root) if runtime is None else runtime

    def plan(self, request: InstallRequest) -> InstallPlan:
        try:
            packages = tuple(system.current_packages(self.root))
        except (OSError, ValueError) as exc:
            raise UnsupportedTargetError(str(exc)) from exc
        target = system.resolve_target(request.user)
        environment = system.user_environment(target, request.overrides)
        system.validate_installation_paths(self.root, target, environment)
        for path in (
            "uv.lock",
            "pyproject.toml",
            "manifests/platforms.toml",
            "assets/hyprland/profile.lua",
            "src/setup_system/_installer_entry.py",
            "src/setup_system/runtime.py",
            "src/setup_system/application/installer.py",
        ):
            if not (self.root / path).is_file():
                raise ValueError("Incomplete local source bundle")
        self.runtime.lock()
        inputs = self.runtime.inputs()
        system.validate_source_access(self.root, target, inputs, run=system.run_command)
        return InstallPlan(target, environment, packages)

    @staticmethod
    def show_plan(plan: InstallPlan) -> None:
        target, environment = plan.target, plan.environment
        print(
            f"Hyprland plan for {target.name} (uid={target.uid}, gid={target.gid}), "
            f"HOME={target.home}"
        )
        print(
            "System: isolated signed Debian refresh; exact backports pins; "
            "no removals or service restarts."
        )
        print("Packages: python3 ca-certificates " + " ".join(plan.packages))
        print(
            f"User: pinned uv {UV_VERSION}/Python {RUNTIME_PYTHON}, "
            "locked runtime, managed files with backups."
        )
        print("User configuration: " + environment["XDG_CONFIG_HOME"])
        print(
            "Preserve network, locale, timezone, login shell and session. "
            "No automatic login/reboot."
        )
        print(
            "Availability/signatures are checked after authorized refresh during apply; "
            "this plan has no writes."
        )

    def _apply_user(self, plan: InstallPlan) -> int:
        os.environ.update(plan.environment)
        runtime = self.runtime.prepare()
        result = system.run_command(
            [
                str(runtime / "bin/setup-system"),
                "workstation",
                "apply",
                "--profile",
                "hyprland",
                "--yes",
            ],
            cwd=plan.target.home,
            timeout=600,
            check=False,
        )
        self._output(result)
        if result.returncode:
            # The fixed internal Typer invocation maps configuration failures to stage exit 5.
            # Public Typer argument validation continues to return its native exit 2.
            print(f"FAILED user configuration command: exit {result.returncode}", file=sys.stderr)
            return 5
        return 0

    @staticmethod
    def _output(result: subprocess.CompletedProcess[str]) -> None:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

    def run(self, request: InstallRequest) -> int:
        if request.operation == Operation.USER_STAGE and os.geteuid() == 0:
            print("Refusing root user-stage.", file=sys.stderr)
            return 2
        if request.operation == Operation.PLAN and request.yes:
            print("--yes belongs to apply.", file=sys.stderr)
            return 2
        try:
            plan = self.plan(request)
        except UnsupportedTargetError as exc:
            print(f"UNSUPPORTED: {exc}. No changes made.", file=sys.stderr)
            return 4
        except (OSError, ValueError) as exc:
            print(f"Preflight failed: {error_text(exc)}", file=sys.stderr)
            return 2
        if request.operation == Operation.USER_STAGE:
            try:
                return self._apply_user(plan)
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
                KeyboardInterrupt,
            ) as exc:
                print(
                    f"FAILED user runtime/configuration: {type(exc).__name__}: {error_text(exc)}. "
                    "Review the prerequisite/conflict and re-run the same command.",
                    file=sys.stderr,
                )
                return 5
        self.show_plan(plan)
        if request.operation == Operation.PLAN:
            return 0
        if os.geteuid() != 0:
            print("Use sudo bash ./setup.sh for installation.", file=sys.stderr)
            return 2
        if not request.yes:
            if not sys.stdin.isatty():
                print("No TTY: apply requires explicit --yes consent.", file=sys.stderr)
                return 2
            if input("Apply the complete plan? [y/N] ").strip().lower() not in {"y", "yes"}:
                return 2
        paths = system.SystemPaths()
        user_started = False
        try:
            system.install_system(self.root, paths=paths)
            print(
                f"Stage: user runtime/configuration (uid={plan.target.uid}; no root build hooks)",
                flush=True,
            )
            user_started = True
            from setup_system.entrypoint import source_arguments

            result = system.run_command(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(self.root / "src/setup_system/_installer_entry.py"),
                    *source_arguments(
                        Operation.USER_STAGE, plan.target.name, plan.environment, yes=True
                    ),
                ],
                cwd=plan.target.home,
                env=plan.environment,
                user=plan.target.uid,
                group=plan.target.gid,
                extra_groups=os.getgrouplist(plan.target.name, plan.target.gid),
                umask=0o077,
                timeout=2400,
                termination_grace=15,
                check=False,
            )
            self._output(result)
            system.mark_user_stage(paths, "complete" if result.returncode == 0 else "failed")
            if result.returncode:
                print(
                    "FAILED user stage. System packages remain installed; "
                    "fix the reported error and re-run the same command.",
                    file=sys.stderr,
                )
                return result.returncode if result.returncode > 0 else 5
        except (
            OSError,
            ValueError,
            RuntimeError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as exc:
            if user_started and paths.journal.exists():
                system.mark_user_stage(paths, "failed")
            journal_note = (
                f"journal: {paths.journal}"
                if paths.journal.is_file() and not paths.journal.is_symlink()
                else "journal not created (failure before the first record)"
            )
            print(
                f"FAILED installation: {type(exc).__name__}: {error_text(exc)}; {journal_note}. "
                "No APT rollback is promised.",
                file=sys.stderr,
            )
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                print(error_text(exc.stderr), file=sys.stderr)
            return 5
        launcher = Path(plan.environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
        recovery = launcher.parent.parent / "runtime/bin/setup-system"
        print("Installation completed; REAL LOGIN/GUI must be checked on the target machine.")
        print(
            "Next: log in locally on a TTY as "
            + plan.target.name
            + " and run: "
            + shlex.quote(str(launcher))
        )
        print("Return to TTY: Super+F9 then Logout; switch consoles with Ctrl+Alt+F3.")
        print("Recovery preview: " + shlex.quote(str(recovery)) + " workstation restore BACKUP_ID")
        return 0
