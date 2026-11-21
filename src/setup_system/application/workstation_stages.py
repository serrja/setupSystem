"""Apply one validated stage from the confirmed workstation plan."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, assert_never

from setup_system.domain.operations import (
    Action,
    ApplicationAction,
    BlockedAction,
    BootAction,
    CleanupAction,
    DesktopAction,
    LanguageAction,
    LoginAction,
    PackageAction,
    RegionAction,
    SystemAction,
    WorkstationPlan,
)
from setup_system.domain.system_commands import (
    BootCommand,
    BootOptions,
    CleanupCommand,
    CleanupOptions,
    LoginCommand,
    LoginOptions,
    PackageOptions,
    PackagesCommand,
    PrivilegedCommand,
    RegionalCommand,
    RegionalOptions,
    SystemCommand,
    SystemOptions,
)


class Executor(Protocol):
    def run(self, command: PrivilegedCommand) -> None: ...


class Stages:
    def __init__(self, root: Path, executor: Executor | None, write: Callable[[str], None]) -> None:
        self.root, self.executor, self.write = root, executor, write

    def privileged(self, command: PrivilegedCommand) -> None:
        if self.executor is None:
            raise ValueError("A verified privileged executor is required")
        self.executor.run(command)

    def apply(self, stage: Action, plan: WorkstationPlan) -> None:
        if isinstance(stage, RegionAction):
            self.privileged(
                RegionalCommand(
                    action=stage.id,
                    settings=RegionalOptions(
                        value=stage.value,
                        before=stage.before,
                        packages=stage.packages,
                        expected_system_sha256=plan.system_revision,
                    ),
                )
            )
        elif isinstance(stage, BootAction):
            self.privileged(
                BootCommand(
                    action="plymouth",
                    settings=BootOptions(
                        identity=stage.identity,
                        packages=stage.packages,
                        expected_system_sha256=plan.system_revision,
                    ),
                )
            )
        elif isinstance(stage, (SystemAction, LoginAction)):
            options = {
                **plan.system.model_dump(),
                "xdg": plan.xdg,
                "expected_system_sha256": plan.system_revision,
            }
            if isinstance(stage, LoginAction):
                login = LoginOptions.model_validate({**options, "confirmed": stage.confirmed})
                self.privileged(LoginCommand(settings=login))
            else:
                self.privileged(
                    SystemCommand(action=stage.id, settings=SystemOptions.model_validate(options))
                )
        elif isinstance(stage, PackageAction):
            self.privileged(
                PackagesCommand(
                    action="workspace_packages",
                    settings=PackageOptions(
                        packages=stage.packages,
                        happ="happ" in plan.user.apps,
                        external_debs=stage.external,
                        expected_happ_version=stage.before,
                    ),
                )
            )
            if {"chatgpt", "happ"}.intersection(plan.user.apps):
                self.write("REQUIRES_USER_LOGIN: finish sign-in or subscription setup in the app")
        elif isinstance(stage, CleanupAction):
            self.privileged(
                CleanupCommand(
                    action="cleanup",
                    settings=CleanupOptions(
                        packages=stage.packages,
                        mode=plan.user.cleanup_mode,
                        closure=stage.packages,
                        xdg=plan.xdg,
                    ),
                )
            )
        elif isinstance(stage, DesktopAction):
            self.desktop(stage, plan)
        elif isinstance(stage, LanguageAction):
            from setup_system.infrastructure.language_tools import apply_language_profile

            apply_language_profile(self.root, stage.id, neovim="neovim" in plan.user.apps)
        elif isinstance(stage, ApplicationAction):
            if stage.id == "neovim":
                from setup_system.infrastructure.editor import apply_editor

                apply_editor(self.root, plan.user)
            elif stage.id == "firefox":
                from setup_system.infrastructure.app_configs import configure_firefox

                configure_firefox()
            else:
                from setup_system.infrastructure.app_configs import configure_codex

                configure_codex(self.root)
        elif isinstance(stage, BlockedAction):
            raise ValueError("Unavailable: " + ", ".join(stage.blockers))
        else:
            assert_never(stage)

    def desktop(self, stage: DesktopAction, plan: WorkstationPlan) -> None:
        from setup_system.application.workstation import apply_desktop, theme_files
        from setup_system.infrastructure.displays import validate_layouts
        from setup_system.infrastructure.managed_files import apply_files

        if plan.user.monitors:
            validate_layouts(plan.user.monitors)
        if stage.id == "desktop_files":
            apply_desktop(
                self.root,
                theme=plan.user.theme,
                keyboard=plan.system.keyboard,
                monitors=plan.user.monitors,
            )
        else:
            apply_files(
                theme_files(
                    self.root,
                    theme=plan.user.theme,
                    keyboard=plan.system.keyboard,
                    monitors=plan.user.monitors,
                )
            )
