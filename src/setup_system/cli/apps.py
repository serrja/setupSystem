"""Applications and installation sources."""

from __future__ import annotations

import subprocess
from typing import Annotated

import typer

from setup_system.application.apps import app_status, catalog, install_zsh
from setup_system.cli._context import ROOT
from setup_system.planning import create_plan

apps_app = typer.Typer(help="Applications and installation sources.")


@apps_app.command("list")
def apps_list() -> None:
    """Show declared sources and actual installation evidence."""
    for name, entry in catalog(ROOT).items():
        versions = ",".join(entry.supported_debian_versions or ()) or "audit-only"
        typer.echo(
            f"{name}: {app_status(entry)} | Debian {versions} | {entry.method} | {entry.source}"
        )


@apps_app.command("install")
def apps_install(
    name: str,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm the selected package action.")
    ] = False,
) -> None:
    """Install one supported Debian package after checking its current state."""
    result = create_plan(ROOT)
    if not result.platform.implemented:
        typer.echo(result.platform.reason, err=True)
        raise typer.Exit(4)
    if result.platform.distribution != "debian":
        typer.echo("UNSUPPORTED: this application adapter is Debian-only.", err=True)
        raise typer.Exit(4)
    entries = catalog(ROOT)
    if name not in entries:
        typer.echo("Unknown catalog application.", err=True)
        raise typer.Exit(2)
    entry = entries[name]
    if (
        entry.supported_debian_versions is None
        or result.platform.version not in entry.supported_debian_versions
    ):
        typer.echo(
            "UNSUPPORTED: no verified Debian provider for this application/version.", err=True
        )
        raise typer.Exit(4)
    status = app_status(entry)
    if status == "unknown":
        typer.echo("blocked: package status could not be established.", err=True)
        raise typer.Exit(4)
    if name == "docker" and status.startswith("present ("):
        typer.echo("blocked: existing Docker binary has no verified package source.", err=True)
        raise typer.Exit(4)
    if name == "docker" and status.startswith("deb "):
        from setup_system.adapters.debian.docker import install_docker

        try:
            typer.echo(install_docker())
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            typer.echo(f"blocked: {exc}", err=True)
            raise typer.Exit(4) from exc
        return
    if status != "absent":
        typer.echo(f"unchanged: {name}: {status}")
        return
    typer.echo(f"{name}: source={entry.source}; method={entry.method}")
    typer.echo(f"privilege={entry.privilege}; data={entry.data}")
    if not yes:
        typer.echo("Use --yes after reviewing the plan.", err=True)
        raise typer.Exit(2)
    if name not in {"zsh", "docker"}:
        typer.echo(
            "blocked: this source requires a separate verified installation adapter.", err=True
        )
        raise typer.Exit(4)
    try:
        if name == "docker":
            from setup_system.adapters.debian.docker import install_docker

            typer.echo(install_docker())
        else:
            typer.echo(install_zsh(entry))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(5) from exc
