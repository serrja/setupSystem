"""Open the packaged Make target from an installed Python distribution."""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from setup_system.execution.authorization import (
    encoded_preferences,
    session_environment,
    ssh_socket,
)
from setup_system.layout import INSTALLED, PACKAGE, resource_root
from setup_system.settings import Settings

cli = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@cli.command()
def open_app(
    show_version: Annotated[
        bool, typer.Option("--version", help="Print the installed version.")
    ] = False,
) -> None:
    """Open the Textual workstation configurator."""
    if show_version:
        if resource_root() == INSTALLED:
            subprocess.run(
                ["/usr/bin/dpkg-query", "-W", r"-f=${Version}\n", PACKAGE], check=True, timeout=10
            )
        else:
            typer.echo(version(PACKAGE))
        return
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        typer.echo("Run setup-system as a normal user", err=True)
        raise typer.Exit(2)
    root = resource_root()
    env = session_environment() | ssh_socket()
    try:
        Settings()
        env["SETUP_SYSTEM_PLUGIN_OVERRIDES"] = encoded_preferences()
    except (OSError, ValueError):
        typer.echo("Invalid host settings. Check the offline settings reference.", err=True)
        raise typer.Exit(1) from None
    for name in Settings.environment_names():
        if name in os.environ:
            env[name] = os.environ[name]
    env["PATH"] = str(Path(sys.executable).parent) + ":/usr/bin:/bin"
    os.chdir(root)
    os.execve("/usr/bin/make", ["make", "-rR", "-j1", "-f", str(root / "Makefile"), "run"], env)


def main() -> None:
    cli()
