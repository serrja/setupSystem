"""Open the Textual interface or migrate the local database."""

import os
from importlib.metadata import version
from typing import Annotated

import anyio
import typer
from platformdirs import user_state_path
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.schemas.errors import Error

from setup_system.errors import OperationError, normalize_failure
from setup_system.journal import Journal
from setup_system.ui.error_presenter import show_cli

cli = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _user() -> None:
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        raise typer.BadParameter("Run setup-system as the database owner")


async def _open() -> None:
    from setup_system.app import SetupApp
    from setup_system.bootstrap import runtime

    if not os.isatty(0) or not os.isatty(1):
        raise OperationError(
            Error(
                code=ErrorCode.CONFIGURATION_INVALID,
                message="An interactive terminal is required.",
                hint="Start setup-system in a terminal, or use --help for commands.",
            )
        )
    async with runtime() as services:
        app = SetupApp(services)
        await app.run_async()
        if app.return_code:
            raise typer.Exit(app.return_code)


@cli.callback(invoke_without_command=True)
def open_app(
    ctx: typer.Context,
    show_version: Annotated[
        bool, typer.Option("--version", is_eager=True, help="Print the installed version.")
    ] = False,
) -> None:
    """Inspect and configure activated plugins."""
    if show_version:
        typer.echo(version("setup-system"))
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _user()
        anyio.run(_open, backend="asyncio")


async def _migrate() -> None:
    from setup_system.configuration.settings import Settings
    from setup_system.infrastructure.database.engine import create_engine
    from setup_system.infrastructure.database.migrations import HEAD, migrate

    engine = create_engine(Settings())
    try:
        backup = await migrate(engine)
        if backup:
            typer.echo(f"Backup saved to {backup}")
        typer.echo(f"Database revision {HEAD}")
    finally:
        await engine.dispose()


@cli.command("migrate")
def migrate_command() -> None:
    """Upgrade the selected database under its exclusive schema lease."""
    _user()
    anyio.run(_migrate, backend="asyncio")


def main() -> None:
    try:
        cli()
    except Exception as failure:
        detail = normalize_failure(failure, stage="startup")
        try:
            Journal(user_state_path("setup-system") / "diagnostics").record_failure(
                detail, failure, stage="startup"
            )
        except Exception:
            pass
        show_cli(detail)
        raise SystemExit(1) from None
