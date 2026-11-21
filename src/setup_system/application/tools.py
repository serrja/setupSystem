"""Non-interactive user use cases exposed only through the workspace Textual screen."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from subprocess import run as run_project
from typing import Protocol

from setup_system.application import templates
from setup_system.domain.tool_requests import (
    TOOL_REQUEST,
    CacheTemplate,
    ConfigureGit,
    InspectGit,
    OpenProject,
    RestoreTool,
    TemplateChoice,
    ToolChoices,
    ToolRequest,
    UseGitProfile,
    WriteTemplate,
    answer_values,
    mutates,
)
from setup_system.infrastructure import copier_templates
from setup_system.infrastructure.project_layout import active_projects, load_layout


def projects(root: Path) -> dict[str, Path]:
    return active_projects(load_layout(root))


class RestoreOperation(Protocol):
    def __call__(self, backup_id: str, *, apply: bool = False) -> list[tuple[str, str]]: ...


@dataclass(frozen=True)
class WorkspaceTools:
    root: Path
    restore: RestoreOperation
    writer: Callable[[], AbstractContextManager[None]]
    history: Callable[[], list[tuple[str, str]]]

    def choices(self) -> ToolChoices:
        return ToolChoices(
            projects=tuple(projects(self.root)),
            templates=tuple(
                TemplateChoice(
                    name,
                    tuple(row.version for row in source.metadata.versions),
                    source.metadata.recommended_version,
                )
                for name, source in templates.load_templates(self.root).items()
            ),
            history=tuple(self.history()),
        )

    def describe(self, request: ToolRequest) -> tuple[str, ...]:
        """Read current targets for the confirmation dialog without modifying them."""
        if request.action == "shell":
            from setup_system.infrastructure.shell import plan_shell

            status, changes = plan_shell(self.root)
            if status.zsh is None:
                raise ValueError("Installed Zsh executable is required")
            return tuple(f"{change}: {path}" for path, change in changes)
        if request.action == "rtk":
            from setup_system.infrastructure.rtk import plan_rtk

            _, plan = plan_rtk(self.root)
            return (plan,)
        if isinstance(request, RestoreTool):
            return (
                self.execute(RestoreTool(action="restore_preview", backup_id=request.backup_id)),
            )
        return tuple(
            str(value)
            for value in request.model_dump(exclude={"action"}, exclude_none=True).values()
            if value
        )

    def execute(self, request: ToolRequest, *, confirmed: bool = False) -> str:
        request = TOOL_REQUEST.validate_python(request)
        if mutates(request) and not confirmed:
            raise ValueError("Explicit Textual confirmation is required")
        if os.geteuid() == 0:
            raise ValueError("Workspace tools must run as the normal user")
        with self.writer() if mutates(request) else nullcontext():
            return _execute(self.root, request, confirmed=confirmed, restore=self.restore)


def _execute(
    root: Path, request: ToolRequest, *, confirmed: bool, restore: RestoreOperation
) -> str:
    if request.action == "projects":
        return "\n".join(name + ": " + str(path) for name, path in projects(root).items())
    if request.action == "prepare_projects":
        from setup_system.infrastructure.project_layout import prepare_owner_directories

        return "\n".join(map(str, prepare_owner_directories(load_layout(root))))
    if isinstance(request, OpenProject):
        available = projects(root)
        if request.name not in available:
            raise ValueError("Select an exact active project name")
        command = (
            ["nvim", "."]
            if request.action == "open_project"
            else [str(Path.home() / ".local/bin/codex")]
        )
        run_project(command, cwd=available[request.name], check=True)
        return str(available[request.name])
    if request.action == "templates":
        sources = templates.load_templates(root)
        return "\n".join(
            f"{name}: " + ", ".join(v.version for v in source.metadata.versions)
            for name, source in sources.items()
        )
    if isinstance(request, WriteTemplate):
        plan = templates.plan_template(
            templates.load_templates(root),
            template=request.name,
            version=request.version,
            destination=request.destination,
            operation="update" if request.action == "update_template" else "create",
            defaults=True,
            data=answer_values(request.answers),
        )
        result = copier_templates.execute_template(plan)
        if result.status == "conflicts":
            raise RuntimeError("Copier conflicts require manual review; working tree preserved")
        return (
            f"{result.status.capitalize()} {result.project_name} at {result.destination}\n"
            f"{result.template} {result.version} · {result.source_commit}"
        )
    if isinstance(request, RestoreTool):
        rows = restore(request.backup_id, apply=confirmed and request.action == "restore")
        return (
            "\n".join(f"{status}: {path}" for path, status in rows)
            or "No recoverable file changes."
        )
    if request.action == "shell":
        from setup_system.infrastructure.shell import apply_shell

        return str(apply_shell(root))
    if request.action == "rtk":
        from setup_system.infrastructure.rtk import install_rtk

        return str(install_rtk(root, update=True))
    if isinstance(request, CacheTemplate):
        plan = templates.plan_template(
            templates.load_templates(root),
            template=request.name,
            version=request.version,
            destination=Path.home(),
            defaults=True,
        )
        return str(copier_templates.prepare_cache(plan))
    if request.action in {"font_plan", "font_apply", "font_probe"}:
        from setup_system.application import workstation

        if request.action == "font_probe":
            return "\n".join(workstation.FONT_PROBE)
        if request.action == "font_plan":
            return workstation.plan_fonts(root)
        return str(workstation.apply_fonts(root))
    if request.action == "git_profiles" or isinstance(request, (ConfigureGit, UseGitProfile)):
        from setup_system.infrastructure import git_profiles

        if request.action == "git_profiles":
            return "\n".join(f"{name}: {path}" for name, path in git_profiles.list_profiles())
        if isinstance(request, UseGitProfile):
            return str(git_profiles.use_profile(request.name, request.destination, yes=confirmed))
        assert isinstance(request, ConfigureGit)
        return str(
            git_profiles.configure_profile(
                request.name,
                request.destination,
                [request.owner_directory] if request.owner_directory else [],
                yes=confirmed,
            )
        )
    if isinstance(request, InspectGit):
        from setup_system.infrastructure.git_profiles import inspect_repository

        return "\n".join(
            key + ": " + value for key, value in inspect_repository(request.destination)
        )
    raise ValueError("Unknown workspace operation")
