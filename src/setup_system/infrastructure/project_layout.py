"""Canonical catalog discovery and explicit owner-directory preparation."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

from pydantic import ValidationError

from setup_system.domain.projects import Project, ProjectCatalog, ProjectLayout
from setup_system.infrastructure.managed_files import apply_files


def load_projects(root: Path) -> ProjectCatalog:
    with (root / "config/workspace/projects.toml").open("rb") as stream:
        return ProjectCatalog.model_validate(tomllib.load(stream))


def load_layout(root: Path, *, home: Path | None = None) -> ProjectLayout:
    """Bind validated catalog entries to a user's actual home directory."""
    home = Path.home() if home is None else home
    try:
        catalog = load_projects(root)
    except ValidationError as error:
        problem = error.errors(include_input=False, include_context=False)[0]
        location = problem["loc"]
        if problem["type"] == "value_error":
            message = problem["msg"].removeprefix("Value error, ")
        elif location and location[0] == "ownerGroups":
            message = "Invalid owner group"
        elif location and location[-1] == "id":
            message = "Invalid project ID"
        else:
            message = "Unsupported project catalog schema or structure"
        raise ValueError(message) from None
    projects_root = home / catalog.projects_root.removeprefix("$HOME/")
    projects = tuple(
        Project(id=record.id, target=projects_root / record.id, origin=record.origin)
        for record in catalog.project
    )
    return ProjectLayout(
        projects_root=projects_root,
        owner_groups=catalog.owner_groups,
        projects=projects,
    )


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
        allowed = {
            f"git@github.com:{project.id}.git",
            f"https://github.com/{project.id}.git",
        }
        fetch_urls = _git(project.target, "remote", "get-url", "--all", "origin", required=False)
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


def owner_directories(layout: ProjectLayout) -> tuple[Path, ...]:
    """Validate the declared root and owner groups before any writes."""
    paths = (layout.projects_root, *(layout.projects_root / group for group in layout.owner_groups))
    for path in paths:
        if _symlink_component(path) is not None or (path.exists() and not path.is_dir()):
            raise ValueError(f"Unsafe layout path: {path}")
        if (path / ".git").exists():
            raise ValueError(f"Layout parent cannot be a Git repository: {path}")
    return paths


def prepare_owner_directories(layout: ProjectLayout) -> tuple[Path, ...]:
    paths = owner_directories(layout)
    apply_files({}, directories=paths)
    return paths
