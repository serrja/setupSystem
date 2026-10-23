"""Explicit native Git author profile commands; no credential management."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from setup_system.application import git_profiles

app = typer.Typer(help="Git author configuration; authentication remains with Git.")
profiles = typer.Typer(help="Private author profiles and directory defaults.", no_args_is_help=True)
app.add_typer(profiles, name="profile")


def _result(result: tuple[Path | None, list[str]], *, yes: bool) -> None:
    backup, actions = result
    for action in actions:
        typer.echo(action)
    if backup is not None:
        typer.echo(f"private backup: {backup}")
    elif not yes and actions != ["unchanged"]:
        typer.echo("Read-only plan; use --yes to apply these exact targets.")


@profiles.command("list")
def list_profiles() -> None:
    """List private profile names and paths, without publishing identity values."""
    try:
        entries = git_profiles.list_profiles()
        for name, path in entries:
            typer.echo(f"{name}: {path}")
        if not entries:
            typer.echo("No profiles; configure one from an already configured repository.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@profiles.command("show")
def show_profile(repo: Annotated[Path, typer.Option(help="Exact existing checkout root.")]) -> None:
    """Show Git author/signing settings with scope and origin; no auth check."""
    try:
        for key, value in git_profiles.inspect_repository(repo):
            typer.echo(f"{key}: {value}")
        typer.echo("Last value for each key wins; environment overrides may still apply.")
        typer.echo("Author configuration does not verify SSH or credential-manager authentication.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@profiles.command("configure")
def configure_profile(
    name: str,
    source_repo: Annotated[
        Path, typer.Option(help="Copy this repository's confirmed Git identity.")
    ],
    directory: Annotated[
        list[Path], typer.Option(help="Existing directory; repeat for each default.")
    ],
    yes: Annotated[
        bool, typer.Option("--yes", help="Back up and apply the reviewed plan.")
    ] = False,
) -> None:
    """Save a private identity and native includeIf defaults; preserve global defaults."""
    try:
        _result(git_profiles.configure_profile(name, source_repo, directory, yes=yes), yes=yes)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@profiles.command("use")
def use_profile(
    name: str,
    repo: Annotated[Path, typer.Option(help="Exact checkout for a local author override.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Back up and apply the local override.")
    ] = False,
) -> None:
    """Override only this repository's author fields, independently of its branch."""
    try:
        _result(git_profiles.use_profile(name, repo, yes=yes), yes=yes)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc


@profiles.command("restore")
def restore_profile(
    backup_id: str,
    yes: Annotated[
        bool, typer.Option("--yes", help="Restore only when current hashes match.")
    ] = False,
) -> None:
    """Preview or restore one private receipt, refusing to overwrite later edits."""
    try:
        for path, action in git_profiles.restore_profile(backup_id, yes=yes):
            typer.echo(f"{action}: {path}")
        if not yes:
            typer.echo("Read-only preview; use --yes to restore.")
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
