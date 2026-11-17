"""Portable Zsh setup for the current user."""

from __future__ import annotations

from typing import Annotated

import typer

from setup_system.application.shell import (
    configure_login_shell,
    plan_shell,
    restore_shell,
)
from setup_system.cli._context import ROOT
from setup_system.cli._interaction import TyperInteraction

shell_app = typer.Typer(help="Portable Zsh setup for the current user.")


@shell_app.command("plan")
def shell_plan() -> None:
    """Inspect Zsh startup changes without modifying the host."""
    try:
        status, changes = plan_shell(ROOT)
    except (OSError, ValueError) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    typer.echo(f"account: {status.user}; login shell: {status.login_shell}")
    typer.echo(f"Zsh: {status.zsh or 'absent'}; registered: {status.registered}")
    typer.echo(f"startup directory: {status.startup_dir}")
    for path, result in changes:
        typer.echo(f"{result}: {path}")


@shell_app.command("apply")
def shell_apply(
    yes: Annotated[bool, typer.Option("--yes", help="Apply the reviewed Zsh plan.")] = False,
) -> None:
    """Back up and apply Zsh files, then use chsh when registered."""
    if not yes:
        shell_plan()
        typer.echo("Use --yes after reviewing the plan.", err=True)
        raise typer.Exit(2)
    code = configure_login_shell(ROOT, TyperInteraction())
    if code:
        raise typer.Exit(code)


@shell_app.command("doctor")
def shell_doctor() -> None:
    """Read back account and startup state without side effects."""
    shell_plan()


@shell_app.command("restore")
def shell_restore(
    backup_id: Annotated[str, typer.Option("--backup", help="Shell apply backup ID.")],
    yes: Annotated[bool, typer.Option("--yes", help="Restore unchanged managed files.")] = False,
) -> None:
    """Restore selected Zsh files while protecting edits made after apply."""
    if not yes:
        typer.echo("Use --yes after reviewing the backup ID.", err=True)
        raise typer.Exit(2)
    try:
        for path, result in restore_shell(backup_id):
            typer.echo(f"{result}: {path}")
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(4) from exc
    typer.echo("Login shell is restored separately with chsh to the saved previous path.")
