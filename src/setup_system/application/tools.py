"""Non-interactive user use cases exposed only through the workspace Textual screen."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from setup_system.application import templates
from setup_system.application.project_layout import active_projects, load_layout

ToolAction = Literal[
    "projects",
    "prepare_projects",
    "open_project",
    "open_codex",
    "templates",
    "create_template",
    "update_template",
    "restore",
    "restore_preview",
    "shell",
    "rtk",
    "git_inspect",
    "git_profiles",
    "git_configure",
    "git_use",
    "template_cache",
    "font_plan",
    "font_apply",
    "font_probe",
]


@dataclass(frozen=True)
class ToolRequest:
    action: ToolAction
    name: str = ""
    destination: Path | None = None
    version: str | None = None
    answers: dict[str, JsonValue] = field(default_factory=dict)
    backup_id: str = ""
    owner_directory: Path | None = None


MUTATING_TOOLS = frozenset(
    {
        "prepare_projects",
        "open_project",
        "open_codex",
        "create_template",
        "update_template",
        "restore",
        "shell",
        "rtk",
        "git_configure",
        "git_use",
        "template_cache",
        "font_apply",
    }
)


def projects(root: Path) -> dict[str, Path]:
    return active_projects(load_layout(root))


def execute(root: Path, request: ToolRequest, *, confirmed: bool = False) -> str:
    if request.action in MUTATING_TOOLS and not confirmed:
        raise ValueError("Explicit Textual confirmation is required")
    if os.geteuid() == 0:
        raise ValueError("Workspace tools must run as the normal user")
    if request.action in MUTATING_TOOLS:
        from setup_system.infrastructure.mutation import user_operation

        with user_operation(Path.home()):
            return _execute(root, request, confirmed=confirmed)
    return _execute(root, request, confirmed=confirmed)


def _execute(root: Path, request: ToolRequest, *, confirmed: bool) -> str:
    if request.action == "projects":
        return "\n".join(name + ": " + str(path) for name, path in projects(root).items())
    if request.action == "prepare_projects":
        from setup_system.application.project_layout import prepare_owner_directories

        return "\n".join(map(str, prepare_owner_directories(load_layout(root))))
    if request.action in {"open_project", "open_codex"}:
        available = projects(root)
        if request.name not in available:
            raise ValueError("Select an exact active project name")
        command = (
            ["nvim", "."]
            if request.action == "open_project"
            else [str(Path.home() / ".local/bin/codex")]
        )
        subprocess.run(command, cwd=available[request.name], check=True)
        return str(available[request.name])
    if request.action == "templates":
        sources = templates.load_templates(root)
        return "\n".join(
            f"{name}: " + ", ".join(v.version for v in source.metadata.versions)
            for name, source in sources.items()
        )
    if request.action in {"create_template", "update_template"}:
        if request.destination is None:
            raise ValueError("An explicit destination is required")
        plan = templates.plan_template(
            templates.load_templates(root),
            template=request.name,
            version=request.version,
            destination=request.destination,
            operation="update" if request.action == "update_template" else "create",
            defaults=True,
            data=request.answers,
        )
        result = templates.execute_template(plan)
        if result.status == "conflicts":
            raise RuntimeError("Copier conflicts require manual review; working tree preserved")
        return (
            f"{result.status.capitalize()} {result.project_name} at {result.destination}\n"
            f"{result.template} {result.version} · {result.source_commit}"
        )
    if request.action in {"restore", "restore_preview"}:
        from setup_system.storage import restore

        rows = restore(request.backup_id, apply=confirmed and request.action == "restore")
        return (
            "\n".join(f"{status}: {path}" for path, status in rows)
            or "No recoverable file changes."
        )
    if request.action == "shell":
        from setup_system.application.shell import apply_shell

        return str(apply_shell(root))
    if request.action == "rtk":
        from setup_system.application.rtk import install_rtk

        return str(install_rtk(root, update=True))
    if request.action == "template_cache":
        plan = templates.plan_template(
            templates.load_templates(root),
            template=request.name,
            version=request.version,
            destination=request.destination or Path.home(),
            defaults=True,
        )
        return str(templates.prepare_cache(plan))
    if request.action in {"font_plan", "font_apply", "font_probe"}:
        from setup_system.application import workstation

        if request.action == "font_probe":
            return "\n".join(workstation.FONT_PROBE)
        if request.action == "font_plan":
            return workstation.plan_fonts(root)
        return str(workstation.apply_fonts(root))
    if request.action in {"git_profiles", "git_configure", "git_use"}:
        from setup_system.application import git_profiles

        if request.action == "git_profiles":
            return "\n".join(f"{name}: {path}" for name, path in git_profiles.list_profiles())
        if request.destination is None:
            raise ValueError("An exact repository root is required")
        if request.action == "git_use":
            return str(git_profiles.use_profile(request.name, request.destination, yes=confirmed))
        return str(
            git_profiles.configure_profile(
                request.name,
                request.destination,
                [request.owner_directory] if request.owner_directory else [],
                yes=confirmed,
            )
        )
    if request.action == "git_inspect":
        from setup_system.application.git_profiles import inspect_repository

        if request.destination is None:
            raise ValueError("An exact repository root is required")
        return "\n".join(
            key + ": " + value for key, value in inspect_repository(request.destination)
        )
    raise ValueError("Unknown workspace operation")
