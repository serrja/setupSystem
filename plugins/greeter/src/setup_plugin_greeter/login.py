"""Apply a confirmed graphical login with service and boot recovery."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from setup_system.execution.account_identity import TargetUser
from setup_system.execution.identity import validate_user_path
from setup_system.execution.processes import run_command
from setup_system.execution.system_facts import SystemSnapshot

from .login_boot import manager_enabled
from .login_files import (
    DesktopPaths,
    LoginFiles,
    LoginFileState,
    LoginReceipt,
)
from .login_plan import default_target, plan_login
from .login_recovery import LoginRecovery
from .models import DesktopPlan, LoginConsent

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Graphical login and boot configuration require root")


class DesktopSystem:
    """Apply a checked login plan with bounded backup and conflict handling."""

    def __init__(
        self,
        root: Path,
        *,
        run: Runner = run_command,
        paths: DesktopPaths | None = None,
        owner_uid: int = 0,
    ) -> None:
        self.root = root
        self.run = run
        self.paths = DesktopPaths() if paths is None else paths
        self.files = LoginFiles(self.paths, owner_uid)

    def _command(
        self, arguments: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = self.run(
            arguments,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
            discard_output=arguments[-1] != "get-default",
            discard_stderr=True,
            timeout=120,
            check=check,
        )
        if check and result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, arguments, result.stdout, result.stderr
            )
        return result

    def plan(
        self,
        target: TargetUser,
        environment: Mapping[str, str],
        *,
        snapshot: SystemSnapshot | None = None,
    ) -> DesktopPlan:
        return plan_login(self.root, self.files, self._command, target, environment, snapshot)

    def revalidate(
        self,
        confirmed: LoginConsent,
        target: TargetUser,
        environment: Mapping[str, str],
        *,
        snapshot: SystemSnapshot | None = None,
    ) -> DesktopPlan:
        """Check original consent and resolve root-only facts before the first write."""
        _require_root()
        fresh = self.plan(target, environment, snapshot=snapshot)
        confirmed.verify(fresh.consent)
        return fresh

    def apply(self, plan: DesktopPlan) -> bool:
        _require_root()
        if plan.consent.deferred:
            raise ValueError("Desktop plan needs a privileged revalidation before apply")
        already_enabled = manager_enabled(self.files)
        if (
            already_enabled != plan.consent.manager_enabled
            or default_target(self._command) != plan.previous_target
            or self.files.fingerprint(self.paths.receipt) != plan.receipt_observed
        ):
            raise ValueError("Display manager, target or receipt changed after planning")
        for item in plan.files:
            digest = self.files.fingerprint(item.path)
            current_mode = None if digest is None else stat.S_IMODE(item.path.lstat().st_mode)
            if (digest, current_mode) != (item.observed, item.observed_mode):
                raise ValueError("Managed desktop destination changed after planning")
        launcher = validate_user_path(
            Path(plan.environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland", plan.target
        )
        if not launcher.is_file():
            raise ValueError("The selected user session launcher is missing or unsafe")
        info = launcher.lstat()
        if (
            info.st_uid != plan.target.uid
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or not info.st_mode & stat.S_IXUSR
        ):
            raise ValueError("The selected user session launcher is missing or unsafe")
        changed = any(
            item.observed != hashlib.sha256(item.content).hexdigest()
            or stat.S_IMODE(item.path.stat().st_mode) != item.mode
            for item in plan.files
        )
        if not changed and already_enabled and plan.previous_target == "graphical.target":
            return False
        recovery = LoginRecovery(self.files, plan.previous_target, already_enabled)
        enabled = False
        default_changed = False
        try:
            for item in plan.files:
                recovery.publish(
                    item.path, item.content, item.mode, item.observed, item.observed_mode
                )
            if changed:
                self._command(
                    [
                        "/usr/bin/systemctl",
                        "--system",
                        "--no-pager",
                        "--no-ask-password",
                        "daemon-reload",
                    ]
                )
            if not manager_enabled(self.files):
                enabled = True
                self._command(
                    [
                        "/usr/bin/systemctl",
                        "--system",
                        "--no-pager",
                        "--no-ask-password",
                        "enable",
                        "greetd.service",
                    ]
                )
            if default_target(self._command) != plan.previous_target:
                raise ValueError("System boot target changed after consent")
            if plan.previous_target != "graphical.target":
                default_changed = True
                self._command(
                    [
                        "/usr/bin/systemctl",
                        "--system",
                        "--no-pager",
                        "--no-ask-password",
                        "set-default",
                        "graphical.target",
                    ]
                )
            previous = self.files.receipt()
            records = dict(previous.files) if previous is not None else {}
            records.update(
                {
                    str(item.path): LoginFileState(
                        sha256=hashlib.sha256(item.content).hexdigest(), mode=item.mode
                    )
                    for item in plan.files
                }
            )
            receipt = LoginReceipt.model_validate(
                {
                    "schema": 1,
                    "owner": "setupSystem",
                    "files": records,
                    "grub_sha256": previous.grub_sha256 if previous else None,
                    "grub_inputs_sha256": previous.grub_inputs_sha256 if previous else None,
                }
            )
            content = (receipt.model_dump_json(indent=2, by_alias=True) + "\n").encode()
            receipt_mode = (
                None
                if plan.receipt_observed is None
                else stat.S_IMODE(self.paths.receipt.stat().st_mode)
            )
            recovery.publish(
                self.paths.receipt, content, 0o600, plan.receipt_observed, receipt_mode
            )
            if self.files.read(self.paths.receipt) != content:
                raise RuntimeError("Login receipt readback failed")
            recovery.checkpoint("complete")
            return True
        except BaseException as error:
            errors = recovery.restore()
            commands: list[list[str]] = []
            if default_changed:
                current = None
                try:
                    current = self._command(
                        ["/usr/bin/systemctl", "--system", "get-default"]
                    ).stdout.strip()
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    errors.append(str(exc))
                if current not in {plan.previous_target, "graphical.target"}:
                    errors.append("System boot target changed during rollback")
                if current == "graphical.target":
                    commands.append(
                        ["/usr/bin/systemctl", "--system", "set-default", plan.previous_target]
                    )
            if enabled:
                try:
                    if manager_enabled(self.files):
                        commands.append(
                            ["/usr/bin/systemctl", "--system", "disable", "greetd.service"]
                        )
                except (OSError, ValueError) as exc:
                    errors.append(str(exc))
            commands.append(["/usr/bin/systemctl", "--system", "daemon-reload"])
            for arguments in commands:
                try:
                    self._command(arguments)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    errors.append(type(exc).__name__)
            recovery.checkpoint("partial" if errors else "restored")
            if errors:
                raise RuntimeError(
                    "Desktop stage failed; file recovery incomplete: " + ", ".join(errors)
                ) from error
            raise
