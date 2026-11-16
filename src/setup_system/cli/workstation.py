"""Portable fonts and managed Hyprland configuration."""

from __future__ import annotations

import subprocess

import typer

from setup_system.application import workstation
from setup_system.cli._context import ROOT

workstation_app = typer.Typer(help="Portable fonts and managed Hyprland configuration.")


@workstation_app.command("plan")
@workstation_app.command("doctor")
def workstation_plan(
    profile: workstation.Profile = "fonts", terminal_profile: str | None = None
) -> None:
    """Print a read-only plan; real GUI verification is a separate manual step."""
    typer.echo(
        workstation.plan(ROOT, profile, terminal_profile=terminal_profile).model_dump_json(indent=2)
    )


@workstation_app.command("font-probe")
def workstation_font_probe() -> None:
    """Print literal comparison text in the current terminal without changing settings."""
    typer.echo("\n".join(workstation.FONT_PROBE))


@workstation_app.command("apply")
def workstation_apply(
    profile: workstation.Profile = "fonts",
    yes: bool = False,
    gnome: bool = False,
    terminal_profile: str | None = None,
    reset_terminal_cell_spacing: bool = False,
    refresh_terminal_font: bool = False,
) -> None:
    """Back up and apply selected user files after reviewing the configuration plan."""
    if not yes:
        raise typer.BadParameter("Review workstation plan, then pass --yes to apply")
    try:
        folder = workstation.apply(
            ROOT,
            profile,
            gnome=gnome,
            terminal_profile=terminal_profile,
            reset_terminal_cell_spacing=reset_terminal_cell_spacing,
            refresh_terminal_font=refresh_terminal_font,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"backup: {folder}; GUI login/validation: not performed")


@workstation_app.command("backup")
def workstation_backup(profile: workstation.Profile = "fonts") -> None:
    """Back up existing profile files without applying them."""
    typer.echo(str(workstation.backup(ROOT, profile)))


@workstation_app.command("restore")
def workstation_restore(backup_id: str, apply: bool = False) -> None:
    """Preview rollback; --apply restores only unchanged managed targets."""
    for path, status in workstation.restore_profile(backup_id, apply_changes=apply):
        typer.echo(f"{status}: {path}")
