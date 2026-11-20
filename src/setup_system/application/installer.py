"""App-owned plan/apply service; viewing and saving intentions never elevate privileges."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock

from setup_system.application.interaction import ConsoleInteraction, Interaction
from setup_system.application.workstation_stages import Executor, Stages, execute
from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.domain.operations import Action, WorkstationPlan
from setup_system.domain.settings import Section, SystemSettings, UserSettings
from setup_system.infrastructure.config_store import ConfigStore, SettingsPaths
from setup_system.infrastructure.privileged import PrivilegedExecutor
from setup_system.installation import TargetUser


class Operation(StrEnum):
    PLAN = "plan"
    APPLY = "apply"


@dataclass(frozen=True)
class InstallRequest:
    operation: Operation
    user: str | None = None
    yes: bool = False
    overrides: dict[str, Path] = field(default_factory=dict)
    timezone: str | None = None
    section: Section = Section.BASE
    system: SystemSettings = field(default_factory=SystemSettings)
    settings: UserSettings = field(default_factory=UserSettings)
    revision_known: bool = False
    draft_revision: str | None = None
    user_revision: str | None = None
    system_revision: str | None = None


class InstallationService:
    """Keep one immutable running plan across navigation, with boundary cancellation."""

    def __init__(
        self,
        root: Path,
        *,
        interaction: Interaction | None = None,
        executor: Executor | None = None,
        authorize: Callable[[], bool] | None = None,
        identity: Callable[[str | None], TargetUser] | None = None,
    ) -> None:
        self.root = root
        self.identity = identity
        self.interaction = ConsoleInteraction() if interaction is None else interaction
        self.paths = SettingsPaths()
        self.cancel = Event()
        self._mutation = Lock()
        self.completed: list[str] = []
        self.status = "Ready"
        self.authorize = authorize
        self.executor = executor
        if executor is None:
            marker = self.paths.state / "release.json"
            if marker.is_file() and not marker.is_symlink() and marker.stat().st_size < 8192:
                record = json.loads(marker.read_text())
                self.executor = PrivilegedExecutor(record["release"], record["source_sha"])

    def plan(self, request: InstallRequest) -> WorkstationPlan:

        from setup_system import installation as system
        from setup_system.infrastructure.inspection import observations

        target = (
            system.resolve_target(request.user)
            if self.identity is None
            else self.identity(request.user)
        )
        overrides = {
            kind: Path(os.environ[f"XDG_{kind.upper()}_HOME"])
            for kind in ("config", "data", "state", "cache")
            if f"XDG_{kind.upper()}_HOME" in os.environ
        }
        overrides.update(request.overrides)
        environment = system.user_environment(target, overrides)
        from setup_system.application.workstation_plan import (
            desktop_changes,
            platform_blocker,
            regional_actions,
            workspace_actions,
        )

        saved_system = None
        revision = draft_revision = None
        if request.section == Section.BASE:
            saved_system, revision = ConfigStore(self.paths.system, SystemSettings, uid=0).read()
            _, draft_revision = self._draft_store().read()
        _, user_revision = ConfigStore(self.paths.user, UserSettings).read()
        if request.revision_known and (
            (
                request.section == Section.BASE
                and (
                    revision != request.system_revision or draft_revision != request.draft_revision
                )
            )
            or user_revision != request.user_revision
        ):
            raise ValueError("Settings changed since inspection; reload before applying")
        release = platform.freedesktop_os_release()
        user_blocked = platform_blocker()
        blocked = user_blocked
        if self.executor is None:
            blocked += ("System apply requires a published release with native build provenance",)
        matched = False
        if request.section == Section.BASE:
            actions, matched = regional_actions(request.system, blocked)
            if request.system.hyprland:
                from setup_system.adapters.debian.hyprland import package_arguments

                try:
                    packages = package_arguments(
                        root=self.root, graphical_login=request.system.graphical_login
                    )
                    reasons = blocked
                except ValueError as exc:
                    packages = []
                    reasons = (*blocked, str(exc))
                actions.append(
                    Action(
                        "base",
                        "Signed official Debian/backports packages: " + " ".join(packages),
                        True,
                        reasons,
                        handler="system",
                    )
                )
                drift = desktop_changes(
                    self.root, request.system, request.settings, user_blocked, full=True
                )
                if drift:
                    actions.append(drift)
                if request.system.graphical_login:
                    actions.append(
                        Action(
                            "login",
                            "Enable greetd with the verified launcher and a visible GRUB menu",
                            True,
                            blocked,
                            handler="system",
                            requires=(("desktop_files",) if drift else ("base",)),
                        )
                    )
            elif request.system.graphical_login:
                actions.append(
                    Action(
                        "login", "Graphical login", True, ("Select Hyprland before applying login",)
                    )
                )
            if request.system.plymouth:
                from setup_system.infrastructure.graphical_unlock import inspect_unlock

                boot = inspect_unlock()
                actions.append(
                    Action(
                        "plymouth",
                        boot.description,
                        True,
                        (*blocked, *boot.blockers),
                        before=boot.identity,
                        packages=boot.packages,
                    )
                )
            if actions and request.system != saved_system:
                actions.append(
                    Action(
                        "system_settings",
                        "Save the verified system selection",
                        True,
                        blocked,
                        handler="system",
                        requires=tuple(item.id for item in actions),
                    )
                )
        else:
            actions, matched = workspace_actions(
                self.root, request.system, request.settings, blocked, user_blocked
            )
        return WorkstationPlan(
            request.section,
            request.system,
            request.settings,
            tuple(actions),
            (
                f"Machine: {release.get('PRETTY_NAME', 'unknown')} / {platform.machine()}",
                "Network, SSH, existing sessions, disks and native password prompts are preserved.",
                *observations(target.home),
                "GPU, monitor/HiDPI, boot/unlock and GUI success require a real target smoke test.",
            ),
            environment=tuple(
                (kind, environment[f"XDG_{kind.upper()}_HOME"])
                for kind in ("config", "data", "state", "cache")
            ),
            system_revision=revision,
            draft_revision=draft_revision,
            user_revision=user_revision,
            matched=matched,
        )

    def _draft_store(self) -> ConfigStore[SystemSettings]:
        return ConfigStore(self.paths.user.parent / "system-intentions.toml", SystemSettings)

    def show_plan(self, plan: WorkstationPlan) -> None:
        for line in plan.describe():
            self.interaction.write(line)

    def run(self, request: InstallRequest) -> int:
        try:
            plan = self.plan(request)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.status = "Failed"
            self.interaction.write(
                "Preflight failed; no operation started: " + error_text(exc), error=True
            )
            return 2
        try:
            self.show_plan(plan)
            if request.operation == Operation.PLAN:
                self.status = "Blocked" if plan.blockers else "Plan ready"
                return 4 if plan.blockers else 0
            if not self._mutation.acquire(blocking=False):
                self.status = "Busy"
                self.interaction.write("Another mutating operation is already running")
                return 2
            try:
                self.cancel.clear()
                self.completed = []
                if plan.blockers:
                    self.status = "Blocked"
                    return 4
                if not plan.actions:
                    self.status = "No changes" if plan.matched else "Nothing selected"
                    message = (
                        "NO_CHANGES: selected settings match the system"
                        if plan.matched
                        else "NO_SELECTION: choose a component to apply"
                    )
                    self.interaction.write(message)
                    return 0
                if not request.yes and not self.interaction.confirm(
                    "Apply this fixed plan snapshot?\n" + "\n".join(plan.describe())
                ):
                    self.status = "Cancelled"
                    self.interaction.write("CANCELLED before any changes")
                    return 2
                from setup_system.infrastructure.mutation import user_operation

                with user_operation():
                    if ConfigStore(self.paths.user, UserSettings).read()[
                        1
                    ] != plan.user_revision or (
                        plan.section == Section.BASE
                        and (
                            ConfigStore(self.paths.system, SystemSettings, uid=0).read()[1]
                            != plan.system_revision
                            or self._draft_store().read()[1] != plan.draft_revision
                        )
                    ):
                        raise ValueError("Settings changed after plan confirmation")
                    return self._execute(plan)
            finally:
                self._mutation.release()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.status = (
                "Partial" if self.completed or isinstance(exc, PartialApplyError) else "Failed"
            )
            self.interaction.write(
                self.status.upper()
                + ": "
                + error_text(exc)
                + "; completed: "
                + ", ".join(self.completed),
                error=True,
            )
            return 5

    def _execute(self, plan: WorkstationPlan) -> int:
        if any(action.privileged for action in plan.actions):
            if self.authorize is None or not self.authorize():
                self.status = "Cancelled"
                self.interaction.write("Privilege authorization was not granted")
                return 2
        for stage in plan.actions:
            if self.cancel.is_set() or self.interaction.cancelled:
                self.status = "Cancelled"
                self.interaction.write(
                    "CANCELLED at a safe boundary; completed: " + ", ".join(self.completed)
                )
                return 3
            if set(stage.requires) - set(self.completed):
                raise ValueError("Incomplete dependencies for " + stage.id)
            self.interaction.write("Running: " + stage.id)
            self._apply(stage.id, plan)
            self.completed.append(stage.id)
        self.status = "Needs reboot" if "plymouth" in self.completed else "Complete"
        self.interaction.write(
            "NEEDS_REBOOT: reboot manually after checking the boot receipt"
            if self.status == "Needs reboot"
            else self.status
        )
        return 0

    def _apply(self, action: str, plan: WorkstationPlan) -> None:
        stage = next(item for item in plan.actions if item.id == action)
        execute(Stages(self.root, self.executor, self.interaction.write), stage, plan)

    def save_user(self, settings: UserSettings, expected: str | None) -> str:
        return ConfigStore(self.paths.user, UserSettings).save(settings, expected)
