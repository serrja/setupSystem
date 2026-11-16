"""Compose the public CLI from commands owned by each application boundary."""

from setup_system.cli.agents import agents_app
from setup_system.cli.apps import apps_app
from setup_system.cli.git_profiles import app as git_app
from setup_system.cli.happ import happ_app
from setup_system.cli.install import install_app
from setup_system.cli.projects import projects_app
from setup_system.cli.rtk import rtk_app
from setup_system.cli.shell import shell_app
from setup_system.cli.system import app
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
