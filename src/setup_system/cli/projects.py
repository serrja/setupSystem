"""Open one discovered project root."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from setup_system.application.project_layout import (
    active_projects,
    load_layout,
    prepare_owner_directories,
)
from setup_system.cli._context import ROOT

projects_app = typer.Typer(help="Open one discovered project root.")


def _discover_projects() -> dict[str, Path]:
    return dict(sorted(active_projects(load_layout(ROOT)).items()))


@projects_app.command("list")
def projects_list() -> None:
    """List exact owned Git roots from the canonical project catalog."""
    try:
        projects = _discover_projects()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for name, path in projects.items():
        typer.echo(f"{name}: {path}")


@projects_app.command("prepare")
def projects_prepare(
    yes: Annotated[
        bool, typer.Option("--yes", help="Create only owner directories on Debian.")
    ] = False,
) -> None:
    """Explicitly prepare the projects root and owner groups on supported Debian."""
    from setup_system.planning import detect_platform

    platform = detect_platform()
    if not platform.implemented:
        typer.echo(platform.reason, err=True)
        raise typer.Exit(4)
    layout = load_layout(ROOT)
    if not yes:
        for path in (
            layout.projects_root,
            *(layout.projects_root / group for group in layout.owner_groups),
        ):
            typer.echo(f"would create if absent: {path}")
        raise typer.Exit(2)
    try:
        for path in prepare_owner_directories(layout):
            typer.echo(f"present: {path}")
    except (OSError, ValueError) as exc:
        typer.echo(f"layout blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


def _choose_project(projects: dict[str, Path], name: str | None) -> Path | None:
    if name is None:
        if not sys.stdin.isatty():
            raise typer.BadParameter("Pass --name when stdin is not interactive")
        selector = shutil.which("fzf")
        if selector is not None:
            selected = subprocess.run(
                [selector, "--prompt", "Project> "],
                input="\n".join(projects) + "\n",
                stdout=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            name = selected.stdout.strip() if selected.returncode == 0 else ""
        else:
            for key in projects:
                typer.echo(key, err=True)
            sys.stderr.write("Project (empty cancels): ")
            sys.stderr.flush()
            name = sys.stdin.readline().strip()
        if not name:
            return None
    root = projects.get(name)
    if root is None:
        raise typer.BadParameter("Choose an exact name from projects list")
    return root


@projects_app.command("path")
def projects_path(
    name: Annotated[str | None, typer.Option(help="Exact name from projects list.")] = None,
) -> None:
    """Print one selected path for a shell function without changing the shell cwd."""
    try:
        project_root = _choose_project(_discover_projects(), name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if project_root is not None:
        typer.echo(str(project_root))


@projects_app.command("open")
def projects_open(
    name: Annotated[str | None, typer.Option(help="Name from projects list.")] = None,
    tmux: Annotated[bool, typer.Option(help="Open in a named tmux session.")] = False,
) -> None:
    """Launch Neovim from one selected project root."""
    try:
        project_root = _choose_project(_discover_projects(), name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if project_root is None:
        typer.echo("cancelled")
        return
    editor = Path.home() / ".local/bin/nvim"
    if not editor.is_file() and not editor.is_symlink():
        raise typer.BadParameter("Managed Neovim is missing; run apply --profile terminalDev")
    if tmux:
        command = shutil.which("tmux")
        if command is None:
            raise typer.BadParameter("tmux is unavailable")
        label = f"{project_root.parent.name}-{project_root.name}"
        session_name = (
            "dev-" + "".join(character for character in label if character.isalnum())[:30]
        )
        exists = subprocess.run(
            [command, "has-session", "-t", session_name], timeout=5, check=False
        )
        if exists.returncode:
            subprocess.run(
                [
                    command,
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-c",
                    str(project_root),
                    str(editor),
                ],
                timeout=10,
                check=True,
            )
            subprocess.run(
                [command, "new-window", "-t", session_name, "-c", str(project_root)],
                timeout=10,
                check=True,
            )
        raise typer.Exit(
            subprocess.run([command, "attach-session", "-t", session_name], check=False).returncode
        )
    raise typer.Exit(subprocess.run([str(editor)], cwd=project_root, check=False).returncode)
