"""Canonical catalog discovery and explicit owner-directory preparation."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from setup_system.application.project_catalog import ProjectCatalog, ProjectState


@dataclass(frozen=True)
class Project:
    id: str
    target: Path
    origin: str | None
    state: ProjectState


@dataclass(frozen=True)
class ProjectLayout:
    projects_root: Path
    owner_groups: tuple[str, ...]
    projects: tuple[Project, ...]
    verify_origins: bool = False


def _home_path(value: str, home: Path) -> Path:
    if not value.startswith("$HOME/"):
        raise ValueError("Project paths must start with literal $HOME/")
    parts = value[6:].split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Project path has an unsafe component")
    return home.joinpath(*parts)


def load_layout(root: Path, *, home: Path | None = None) -> ProjectLayout:
    """Load one declarative catalog without expanding arbitrary environment data."""
    home = Path.home() if home is None else home
    data = tomllib.loads((root / "manifests/projects.toml").read_text(encoding="utf-8"))
    try:
        catalog = ProjectCatalog.model_validate(data)
    except ValidationError as error:
        locations = [
            item["loc"] for item in error.errors(include_input=False, include_context=False)
        ]
        if any(location and location[0] == "ownerGroups" for location in locations):
            message = "Invalid owner group"
        elif any(location and location[-1] == "state" for location in locations):
            message = "Invalid project state"
        else:
            message = "Unsupported project catalog schema or structure"
        raise ValueError(message) from None
    projects_root = _home_path(catalog.projects_root, home)
    groups = tuple(catalog.owner_groups)
    if not groups or len(groups) != len(set(groups)):
        raise ValueError("Owner groups must be unique")
    projects: list[Project] = []
    seen: set[str] = set()
    for record in catalog.project:
        project_id = record.id
        parts = project_id.split("/")
        if (
            len(parts) != 2
            or parts[0] not in groups
            or not (re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", parts[1]) or parts[1] == ".github")
            or project_id in seen
        ):
            raise ValueError(f"Invalid or repeated project ID: {project_id}")
        seen.add(project_id)
        target = projects_root.joinpath(*parts)
        projects.append(
            Project(
                project_id,
                target,
                record.origin,
                record.state,
            )
        )
    return ProjectLayout(projects_root, groups, tuple(projects), projects_root != home / "Projects")


def _git(path: Path, *args: str, required: bool = True) -> bytes:
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
        capture_output=True,
        timeout=15,
        check=False,
        env=environment,
    )
    if required and result.returncode:
        raise ValueError(f"Git inspection failed at {path}: {' '.join(args)}")
    return result.stdout if result.returncode == 0 else b""


def _symlink_component(path: Path) -> Path | None:
    current = path
    while True:
        if current.is_symlink():
            return current
        if current == current.parent:
            return None
        current = current.parent


def _exact_git_root(path: Path) -> bool:
    if (
        _symlink_component(path) is not None
        or (path / ".git").is_symlink()
        or not path.is_dir()
        or not (path / ".git").exists()
    ):
        return False
    result = _git(path, "rev-parse", "--show-toplevel", required=False).decode().strip()
    return result != "" and Path(result) == path


def active_projects(layout: ProjectLayout) -> dict[str, Path]:
    """Choose a single existing checkout per project, never a directory scan."""
    result: dict[str, Path] = {}
    for project in layout.projects:
        if not _exact_git_root(project.target):
            continue
        if layout.verify_origins:
            allowed = {
                f"git@github.com:{project.id}.git",
                f"https://github.com/{project.id}.git",
            }
            fetch_urls = _git(
                project.target, "remote", "get-url", "--all", "origin", required=False
            )
            push_urls = _git(
                project.target, "remote", "get-url", "--push", "--all", "origin", required=False
            )
            urls = (fetch_urls + push_urls).decode().splitlines()
            if (
                project.origin not in allowed
                or not fetch_urls.strip()
                or not push_urls.strip()
                or any(url not in allowed for url in urls)
            ):
                raise ValueError(
                    f"Explicit project root requires canonical fetch/push origins: {project.id}"
                )
        result[project.id] = project.target
    return result


def prepare_owner_directories(layout: ProjectLayout) -> tuple[Path, ...]:
    """Create only the root and owner groups after an explicit Debian CLI action."""
    paths = (layout.projects_root, *(layout.projects_root / group for group in layout.owner_groups))
    for path in paths:
        if _symlink_component(path) is not None or (path.exists() and not path.is_dir()):
            raise ValueError(f"Unsafe layout path: {path}")
        if (path / ".git").exists():
            raise ValueError(f"Layout parent cannot be a Git repository: {path}")
    for path in paths:
        path.mkdir(exist_ok=True)
    return paths
