"""Trusted executors for the finite stages produced by the workstation planner."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from setup_system.domain.operations import Action, WorkstationPlan


class Executor(Protocol):
    def run(self, action: str, settings: dict[str, object]) -> None: ...


class Stages:
    def __init__(self, root: Path, executor: Executor | None, write: Callable[[str], None]) -> None:
        self.root = root
        self.executor = executor
        self.write = write

    def privileged(self, action: str, options: dict[str, object]) -> None:
        if self.executor is None:
            raise ValueError("A verified privileged executor is required")
        self.executor.run(action, options)

    def region(self, stage: Action, plan: WorkstationPlan) -> None:
        self.privileged(
            stage.id,
            {
                "value": stage.value,
                "packages": list(stage.packages),
                "before": stage.before,
                "expected_system_sha256": plan.system_revision,
            },
        )

    def boot(self, stage: Action, plan: WorkstationPlan) -> None:
        self.privileged(
            "plymouth",
            {
                "identity": stage.before,
                "packages": list(stage.packages),
                "expected_system_sha256": plan.system_revision,
            },
        )

    def system(self, stage: Action, plan: WorkstationPlan) -> None:
        self.privileged(
            stage.id,
            {
                **plan.system.model_dump(mode="json"),
                "xdg": dict(plan.environment),
                "expected_system_sha256": plan.system_revision,
            },
        )

    def packages(self, stage: Action, plan: WorkstationPlan) -> None:
        self.privileged(
            "workspace_packages",
            {
                "packages": list(stage.packages),
                "happ": "happ" in plan.user.apps,
                "external_debs": list(stage.external),
                "expected_happ_version": stage.before,
            },
        )
        if {"chatgpt", "happ"}.intersection(plan.user.apps):
            self.write("REQUIRES_USER_LOGIN: finish sign-in or subscription setup in the app")

    def desktop(self, stage: Action, plan: WorkstationPlan) -> None:
        from setup_system.application.workstation import apply_desktop, theme_files
        from setup_system.infrastructure.displays import validate_layouts
        from setup_system.infrastructure.user_files import apply_files

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

    def editor(self, stage: Action, plan: WorkstationPlan) -> None:
        from setup_system.infrastructure.editor import apply_editor

        apply_editor(self.root, plan.user)

    def language(self, stage: Action, plan: WorkstationPlan) -> None:
        from setup_system.infrastructure.language_tools import apply_language_profile

        apply_language_profile(self.root, stage.id, neovim="neovim" in plan.user.apps)

    def firefox(self, stage: Action, plan: WorkstationPlan) -> None:
        from setup_system.infrastructure.app_configs import configure_firefox

        configure_firefox()

    def codex(self, stage: Action, plan: WorkstationPlan) -> None:
        from setup_system.infrastructure.app_configs import configure_codex

        configure_codex(self.root)

    def cleanup(self, stage: Action, plan: WorkstationPlan) -> None:
        self.privileged(
            "cleanup",
            {
                "packages": list(stage.packages),
                "mode": plan.user.cleanup_mode,
                "closure": list(stage.packages),
                "xdg": dict(plan.environment),
            },
        )


HANDLERS: dict[str, Callable[[Stages, Action, WorkstationPlan], None]] = {
    "region": Stages.region,
    "system": Stages.system,
    "plymouth": Stages.boot,
    "workspace_packages": Stages.packages,
    "desktop": Stages.desktop,
    "neovim": Stages.editor,
    "python_tools": Stages.language,
    "language": Stages.language,
    "firefox": Stages.firefox,
    "codex": Stages.codex,
    "cleanup": Stages.cleanup,
}


def execute(context: Stages, stage: Action, plan: WorkstationPlan) -> None:
    handler = HANDLERS.get(stage.handler or stage.id)
    if handler is None:
        raise ValueError(f"No reviewed executor for {stage.id}")
    handler(context, stage, plan)
