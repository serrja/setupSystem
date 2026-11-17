"""Typer adapter for the shared installation use case."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.cli._context import ROOT

install_app = typer.Typer(help="Plan or apply the one-command workstation installation.")


def _request(
    operation: Operation,
    user: str | None,
    yes: bool,
    paths: tuple[Path | None, ...],
    subscription_file: Path | None = None,
    timezone: str | None = None,
) -> InstallRequest:
    return InstallRequest(
        operation,
        user=user,
        yes=yes,
        subscription_file=subscription_file,
        timezone=timezone,
        overrides={
            kind: path
            for kind, path in zip(("config", "data", "state", "cache"), paths, strict=True)
            if path is not None
        },
    )


@install_app.command("plan")
def install_plan(
    source: Annotated[Path, typer.Option(help="Verified local setupSystem source bundle.")] = ROOT,
    user: str | None = None,
    xdg_config_home: Path | None = None,
    xdg_data_home: Path | None = None,
    xdg_state_home: Path | None = None,
    xdg_cache_home: Path | None = None,
    vpn_config_file: Annotated[
        Path | None,
        typer.Option(
            help="Private HTTPS subscription input file inside verified HOME; never a URL."
        ),
    ] = None,
    timezone: Annotated[
        str | None, typer.Option(help="IANA timezone; omit to preserve current.")
    ] = None,
) -> None:
    """Inspect without preparing prerequisites, refreshing metadata or changing files."""
    request = _request(
        Operation.PLAN,
        user,
        False,
        (xdg_config_home, xdg_data_home, xdg_state_home, xdg_cache_home),
        vpn_config_file,
        timezone,
    )
    raise typer.Exit(InstallationService(source).run(request))


@install_app.command("apply")
def install_apply(
    source: Annotated[Path, typer.Option(help="Verified local setupSystem source bundle.")] = ROOT,
    yes: bool = False,
    user: str | None = None,
    xdg_config_home: Path | None = None,
    xdg_data_home: Path | None = None,
    xdg_state_home: Path | None = None,
    xdg_cache_home: Path | None = None,
    vpn_config_file: Annotated[
        Path | None,
        typer.Option(
            help="Private HTTPS subscription input file inside verified HOME; never a URL."
        ),
    ] = None,
    timezone: Annotated[
        str | None, typer.Option(help="IANA timezone; omit to preserve current.")
    ] = None,
) -> None:
    """Apply the checked installation plan through the shared application service."""
    request = _request(
        Operation.APPLY,
        user,
        yes,
        (xdg_config_home, xdg_data_home, xdg_state_home, xdg_cache_home),
        vpn_config_file,
        timezone,
    )
    raise typer.Exit(InstallationService(source).run(request))
