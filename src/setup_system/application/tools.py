"""Non-interactive user use cases exposed only through the workspace Textual screen."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from setup_system.application import templates
from setup_system.application.project_layout import active_projects, load_layout

ToolAction = Literal[
    "projects",
    "prepare_projects",
    "open_project",
    "templates",
    "create_template",
    "update_template",
    "restore",
    "shell",
    "rtk",
    "git_inspect",
    "git_profiles",
    "git_configure",
    "git_use",
    "git_restore",
    "template_cache",
    "font_plan",
    "font_apply",
    "font_probe",
    "font_restore",
    "shell_restore",
    "rtk_restore",
]


@dataclass(frozen=True)
class ToolRequest:
    action: ToolAction
    name: str = ""
    destination: Path | None = None
    version: str | None = None
    data: str = "{}"
    backup_id: str = ""


MUTATING_TOOLS = frozenset(
    {
        "prepare_projects",
        "open_project",
        "create_template",
        "update_template",
        "restore",
        "shell",
        "rtk",
        "git_configure",
        "git_use",
        "git_restore",
        "template_cache",
        "font_apply",
        "font_restore",
        "shell_restore",
        "rtk_restore",
    }
)


def projects(root: Path) -> dict[str, Path]:
    return active_projects(load_layout(root))


def execute(root: Path, request: ToolRequest, *, confirmed: bool = False) -> str:
    if request.action in MUTATING_TOOLS and not confirmed:
        raise ValueError("Explicit Textual confirmation is required")
    if os.geteuid() == 0:
        raise ValueError("Workspace tools must run as the normal user")
    if request.action == "projects":
        return "\n".join(name + ": " + str(path) for name, path in projects(root).items())
    if request.action == "prepare_projects":
        from setup_system.application.project_layout import prepare_owner_directories

        return "\n".join(map(str, prepare_owner_directories(load_layout(root))))
    if request.action == "open_project":
        available = projects(root)
        if request.name not in available:
            raise ValueError("Select an exact active project name")
        subprocess.run(["nvim", "."], cwd=available[request.name], check=True)
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
        answers = json.loads(request.data)
        if not isinstance(answers, dict):
            raise ValueError("Copier answers must be a JSON object")
        plan = templates.plan_template(
            templates.load_templates(root),
            template=request.name,
            version=request.version,
            destination=request.destination,
            operation="update" if request.action == "update_template" else "create",
            defaults=True,
            data=answers,
        )
        result = templates.execute_template(plan)
        if result.status == "conflicts":
            raise RuntimeError("Copier conflicts require manual review; working tree preserved")
        return result.model_dump_json(indent=2)
    if request.action == "restore":
        from setup_system.storage import restore

        return str(restore(request.backup_id, apply=confirmed))
    if request.action == "shell":
        from setup_system.application.shell import apply_shell

        return str(apply_shell(root))
    if request.action == "rtk":
        from setup_system.application.rtk import install_rtk

        return str(install_rtk(root))
    if request.action == "template_cache":
        plan = templates.plan_template(
            templates.load_templates(root),
            template=request.name,
            version=request.version,
            destination=request.destination or Path.home(),
            defaults=True,
        )
        return str(templates.prepare_cache(plan))
    if request.action in {"font_plan", "font_apply", "font_probe", "font_restore"}:
        from setup_system.application import workstation

        if request.action == "font_probe":
            return "\n".join(workstation.FONT_PROBE)
        if request.action == "font_plan":
            return workstation.plan(
                root, "fonts", terminal_profile=request.name or None
            ).model_dump_json(indent=2)
        if request.action == "font_restore":
            return str(workstation.restore_profile(request.backup_id, apply_changes=confirmed))
        options = json.loads(request.data)
        if (
            not isinstance(options, dict)
            or not set(options) <= {"gnome", "reset_terminal_cell_spacing", "refresh_terminal_font"}
            or any(type(value) is not bool for value in options.values())
        ):
            raise ValueError("Invalid font settings")
        return str(
            workstation.apply(root, "fonts", terminal_profile=request.name or None, **options)
        )
    if request.action in {"git_profiles", "git_configure", "git_use", "git_restore"}:
        from setup_system.application import git_profiles

        if request.action == "git_profiles":
            return str(git_profiles.list_profiles())
        if request.action == "git_restore":
            return str(git_profiles.restore_profile(request.backup_id, yes=confirmed))
        if request.destination is None:
            raise ValueError("An exact repository root is required")
        if request.action == "git_use":
            return str(git_profiles.use_profile(request.name, request.destination, yes=confirmed))
        directories = json.loads(request.data)
        if not isinstance(directories, list) or not all(
            isinstance(item, str) for item in directories
        ):
            raise ValueError("Directory defaults must be an explicit JSON list of paths")
        return str(
            git_profiles.configure_profile(
                request.name,
                request.destination,
                [Path(item).expanduser() for item in directories],
                yes=confirmed,
            )
        )
    if request.action == "shell_restore":
        from setup_system.application.shell import restore_shell

        return str(restore_shell(request.backup_id))
    if request.action == "rtk_restore":
        from setup_system.application.rtk import restore_rtk

        return restore_rtk(request.backup_id)
    if request.action == "git_inspect":
        from setup_system.application.git_profiles import inspect_repository

        if request.destination is None:
            raise ValueError("An exact repository root is required")
        return "\n".join(
            key + ": " + value for key, value in inspect_repository(request.destination)
        )
    raise ValueError("Unknown workspace operation")
