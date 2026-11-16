"""Compose the public CLI from commands owned by each application boundary."""

import sys
from pathlib import Path

import typer

from setup_system.cli._context import ROOT
from setup_system.cli.agents import agents_app
from setup_system.cli.apps import apps_app
from setup_system.cli.git_profiles import app as git_app
from setup_system.cli.happ import happ_app
from setup_system.cli.install import install_app
from setup_system.cli.projects import projects_app
from setup_system.cli.rtk import rtk_app
from setup_system.cli.shell import shell_app
from setup_system.cli.system import app as app
from setup_system.cli.templates import templates_app
from setup_system.cli.workstation import workstation_app

app.add_typer(install_app, name="install")
app.add_typer(rtk_app, name="rtk")
app.add_typer(shell_app, name="shell")
app.add_typer(happ_app, name="happ")
app.add_typer(apps_app, name="apps")
app.add_typer(agents_app, name="agents")
app.add_typer(templates_app, name="templates")
app.add_typer(projects_app, name="projects")
app.add_typer(workstation_app, name="workstation")
app.add_typer(git_app, name="git")


def _terminal_app(source: Path, user: str | None) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        typer.echo("An interactive terminal is required. Use --help or install plan.", err=True)
        raise typer.Exit(2)
    from setup_system.application.installer import InstallRequest, Operation
    from setup_system.tui.installer import InstallationApp

    code = InstallationApp(source.resolve(), InstallRequest(Operation.PLAN, user=user)).run()
    raise typer.Exit(code or 0)


@app.callback(invoke_without_command=True)
def application(
    ctx: typer.Context,
    source: Path = ROOT,
    user: str | None = None,
) -> None:
    """Open the terminal application, or execute an explicit automation command."""
    if ctx.invoked_subcommand is None:
        _terminal_app(source, user)


@app.command("tui")
def terminal_interface(source: Path = ROOT, user: str | None = None) -> None:
    """Open the interactive Textual application without automatically applying changes."""
    _terminal_app(source, user)
