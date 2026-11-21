"""App-owned plan/apply service; viewing and saving intentions never elevate privileges."""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event, Lock

from setup_system.application.interaction import Interaction
from setup_system.application.ui_ports import SettingsPort
from setup_system.application.workstation_stages import Executor, Stages
from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.domain.catalog import ComponentCatalog
from setup_system.domain.identity import TargetUser
from setup_system.domain.login import DesktopPlan
from setup_system.domain.operations import (
    Action,
    BlockedAction,
    BootAction,
    InstallRequest,
    LoginAction,
    Operation,
    RunStatus,
    SystemAction,
    WorkstationPlan,
)
from setup_system.domain.paths import XdgPaths
from setup_system.domain.platforms import CapabilityPolicy
from setup_system.domain.settings import Section


class InstallationService:
    """Keep one immutable running plan across navigation, with boundary cancellation."""

    def __init__(
        self,
        root: Path,
        *,
        catalog: ComponentCatalog,
        settings: SettingsPort,
        policy: CapabilityPolicy,
        login_plan: Callable[[TargetUser, Mapping[str, str]], DesktopPlan],
        privilege_blocker: str,
        interaction: Interaction,
        executor: Executor | None = None,
        authorize: Callable[[], bool] | None = None,
        identity: Callable[[str | None], TargetUser],
    ) -> None:
        self.root = root
        self.identity = identity
        self.interaction = interaction
        self.settings, self.catalog = settings, catalog
        self.policy, self.login_plan = policy, login_plan
        self.cancel = Event()
        self._mutation = Lock()
        self.completed: list[str] = []
        self.status = RunStatus.READY
        self.authorize = authorize
        self.executor = executor
        self.privilege_blocker = privilege_blocker

    def plan(self, request: InstallRequest) -> WorkstationPlan:

        from setup_system.infrastructure import identity
        from setup_system.infrastructure.inspection import observations

        target = self.identity(request.user)
        overrides = {
            kind: Path(os.environ[f"XDG_{kind.upper()}_HOME"])
            for kind in ("config", "data", "state", "cache")
            if f"XDG_{kind.upper()}_HOME" in os.environ
        }
        environment = identity.user_environment(target, overrides)
        from setup_system.application.workstation_plan import (
            desktop_changes,
            platform_blocker,
            regional_actions,
            workspace_actions,
        )

        self.catalog.validate_settings(request.settings)
        saved_system = None
        revision = draft_revision = None
        if request.section == Section.BASE:
            saved_system, revision = self.settings.system()
            _, draft_revision = self.settings.draft()
        _, user_revision = self.settings.user()
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
        user_blocked = platform_blocker(self.policy)
        blocked = user_blocked
        if self.executor is None:
            blocked += (self.privilege_blocker,)
        matched = False
        if request.section == Section.BASE:
            actions, matched = regional_actions(request.system, blocked, supported=not user_blocked)
            if request.system.hyprland:
                from setup_system.adapters.debian.hyprland import package_arguments

                try:
                    packages = package_arguments(
                        root=self.root,
                        graphical_login=request.system.graphical_login,
                        policy=self.policy,
                    )
                    reasons = blocked
                except ValueError as exc:
                    packages = []
                    reasons = (*blocked, str(exc))
                actions.append(
                    SystemAction(
                        id="base",
                        description="Signed official Debian/backports packages: "
                        + " ".join(packages),
                        blockers=reasons,
                    )
                )
                drift = desktop_changes(
                    self.root, request.system, request.settings, user_blocked, full=True
                )
                if drift:
                    actions.append(drift)
                if request.system.graphical_login and user_blocked:
                    actions.append(
                        BlockedAction(id="login", description="Graphical login", blockers=blocked)
                    )
                elif request.system.graphical_login:
                    description = "Enable greetd and keep the GRUB menu visible"
                    try:
                        login = self.login_plan(target, environment)
                        description += f". Boot target {login.previous_target} → graphical.target."
                        if login.consent.deferred:
                            description += (
                                " Apply will verify "
                                + ", ".join(login.consent.deferred)
                                + " with system privileges."
                            )
                        actions.append(
                            LoginAction(
                                description=description,
                                confirmed=login.consent,
                                blockers=blocked,
                                requires=(("desktop_files",) if drift else ("base",)),
                            )
                        )
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        actions.append(
                            BlockedAction(
                                id="login",
                                description=description,
                                blockers=(*blocked, error_text(exc)),
                            )
                        )
            elif request.system.graphical_login:
                actions.append(
                    BlockedAction(
                        id="login",
                        description="Graphical login",
                        blockers=("Select Hyprland before applying login",),
                    )
                )
            if request.system.plymouth:
                from setup_system.infrastructure.graphical_unlock import inspect_unlock

                boot = inspect_unlock()
                if boot.blockers or boot.identity is None:
                    actions.append(
                        BlockedAction(
                            id="plymouth",
                            description=boot.description,
                            blockers=(*blocked, *boot.blockers) or ("Boot identity unavailable",),
                        )
                    )
                else:
                    actions.append(
                        BootAction(
                            description=boot.description,
                            blockers=blocked,
                            identity=boot.identity,
                            packages=boot.packages,
                        )
                    )
            if actions and request.system != saved_system:
                actions.append(
                    SystemAction(
                        id="system_settings",
                        description="Save the verified system selection",
                        blockers=blocked,
                        requires=tuple(item.id for item in actions),
                    )
                )
        else:
            actions, matched = workspace_actions(
                self.root,
                self.catalog,
                self.policy,
                request.system,
                request.settings,
                blocked,
                user_blocked,
            )
        return WorkstationPlan(
            section=request.section,
            system=request.system,
            user=request.settings,
            actions=tuple(actions),
            observations=(
                f"Machine: {release.get('PRETTY_NAME', 'unknown')} / {platform.machine()}",
                "Network, SSH, existing sessions, disks and native password prompts are preserved.",
                *observations(target.home),
                "GPU, monitor/HiDPI, boot/unlock and GUI success require a real target smoke test.",
            ),
            xdg=XdgPaths.model_validate(
                {
                    kind: environment[f"XDG_{kind.upper()}_HOME"]
                    for kind in ("config", "data", "state", "cache")
                }
            ),
            system_revision=revision,
            draft_revision=draft_revision,
            user_revision=user_revision,
            matched=matched,
        )

    def show_plan(self, plan: WorkstationPlan) -> None:
        for line in plan.describe():
            self.interaction.write(line)

    def run(self, request: InstallRequest) -> int:
        try:
            plan = self.plan(request)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.status = RunStatus.FAILED
            self.interaction.write(
                "Preflight failed; no operation started: " + error_text(exc), error=True
            )
            return 2
        try:
            self.show_plan(plan)
            if request.operation == Operation.PLAN:
                self.status = RunStatus.BLOCKED if plan.blockers else RunStatus.PLAN_READY
                return 4 if plan.blockers else 0
            if not self._mutation.acquire(blocking=False):
                self.status = RunStatus.BUSY
                self.interaction.write("Another mutating operation is already running")
                return 2
            try:
                self.cancel.clear()
                self.completed = []
                if plan.blockers:
                    self.status = RunStatus.BLOCKED
                    return 4
                if not plan.actions:
                    self.status = (
                        RunStatus.NO_CHANGES if plan.matched else RunStatus.NOTHING_SELECTED
                    )
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
                    self.status = RunStatus.CANCELLED
                    self.interaction.write("CANCELLED before any changes")
                    return 2
                from setup_system.infrastructure.mutation import user_operation

                with user_operation():
                    if self.settings.user()[1] != plan.user_revision or (
                        plan.section == Section.BASE
                        and (
                            self.settings.system()[1] != plan.system_revision
                            or self.settings.draft()[1] != plan.draft_revision
                        )
                    ):
                        raise ValueError("Settings changed after plan confirmation")
                    return self._execute(plan)
            finally:
                self._mutation.release()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.status = (
                RunStatus.PARTIAL
                if self.completed or isinstance(exc, PartialApplyError)
                else RunStatus.FAILED
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
                self.status = RunStatus.CANCELLED
                self.interaction.write("Privilege authorization was not granted")
                return 2
        for stage in plan.actions:
            if self.cancel.is_set() or self.interaction.cancelled:
                self.status = RunStatus.CANCELLED
                self.interaction.write(
                    "CANCELLED at a safe boundary; completed: " + ", ".join(self.completed)
                )
                return 3
            if set(stage.requires) - set(self.completed):
                raise ValueError("Incomplete dependencies for " + stage.id)
            self.interaction.write("Running: " + stage.id)
            self._apply(stage, plan)
            self.completed.append(stage.id)
        self.status = RunStatus.NEEDS_REBOOT if "plymouth" in self.completed else RunStatus.COMPLETE
        self.interaction.write(
            "NEEDS_REBOOT: reboot manually after checking the boot receipt"
            if self.status == RunStatus.NEEDS_REBOOT
            else self.status
        )
        return 0

    def _apply(self, stage: Action, plan: WorkstationPlan) -> None:
        Stages(self.root, self.executor, self.interaction.write).apply(stage, plan)
