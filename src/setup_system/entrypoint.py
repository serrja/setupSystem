"""Open the packaged Make target from an installed Python distribution."""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from setup_system.layout import INSTALLED, PACKAGE, resource_root
from setup_system.runner import _environment

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
            subprocess.run(["/usr/bin/dpkg-query", "-W", r"-f=${Version}\n", PACKAGE], check=True)
        else:
            typer.echo(version(PACKAGE))
        return
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        typer.echo("Run setup-system as a normal user", err=True)
        raise typer.Exit(2)
    root = resource_root()
    env = _environment()
    env["PATH"] = str(Path(sys.executable).parent) + ":/usr/bin:/bin"
    os.chdir(root)
    os.execve("/usr/bin/make", ["make", "-rR", "-j1", "-f", str(root / "Makefile"), "run"], env)


def main() -> None:
    cli()
