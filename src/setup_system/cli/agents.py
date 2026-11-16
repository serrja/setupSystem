"""Scoped agent configuration."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Annotated

import typer

from setup_system.application.project_layout import (
    active_projects,
    load_layout,
)
from setup_system.cli._context import ROOT

agents_app = typer.Typer(help="Scoped agent configuration.")


@agents_app.command("doctor")
def agents_doctor() -> None:
    """Inspect installed rules and profiles; no sandbox claim is inferred."""
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    rules = codex_home / "AGENTS.md"
    shared = home / ".agents/policy/AGENTS.md"
    typer.echo(f"Codex entry: {'present' if rules.is_file() else 'missing'}")
    typer.echo(f"Shared policy: {'present' if shared.is_file() else 'missing'}")
    matches = rules.is_file() and shared.is_file() and rules.read_bytes() == shared.read_bytes()
    typer.echo(f"Entry matches policy: {matches}")
    for name in ("review", "project", "network"):
        path = codex_home / f"{name}.config.toml"
        typer.echo(f"{name} profile: {'present' if path.is_file() else 'missing'}")
    typer.echo("Permission enforcement requires separate sentinel probes in a new scoped session.")


@agents_app.command("launch")
def agents_launch(
    scope: str,
    mode: Annotated[str, typer.Option(help="review, project or network.")] = "review",
) -> None:
    """Open Codex in one allowlisted project root with a named permission profile."""
    if mode not in {"review", "project", "network"}:
        raise typer.BadParameter("Choose review, project or network")
    layout = load_layout(ROOT)
    try:
        projects = active_projects(layout)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if scope in {project.id for project in layout.projects}:
        root = projects.get(scope)
        if root is None:
            raise typer.BadParameter("Catalog project is missing or has no exact Git root")
    else:
        if scope in {project.id.split("/", 1)[1] for project in layout.projects}:
            raise typer.BadParameter("Use the owner/project ID from projects list")
        config = (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "agents/scopes.toml"
        )
        data = tomllib.loads(config.read_text()) if config.is_file() else {}
        record = data.get("scope", {}).get(scope)
        if not record:
            raise typer.BadParameter("Unknown local scope")
        root = Path(record["path"]).resolve()
    if not root.is_dir() or root == Path.home().resolve():
        raise typer.BadParameter("Scope is missing or too broad")
    git = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if git.returncode != 0 or Path(git.stdout.strip()).resolve() != root:
        raise typer.BadParameter("Scope is not an exact Git root")
    executable = shutil.which("codex")
    if not executable:
        raise typer.BadParameter("Codex is unavailable")
    raise typer.Exit(
        subprocess.run([executable, "--profile", mode, "--cd", str(root)], check=False).returncode
    )
