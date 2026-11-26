"""Alembic entry points for Textual startup and the fixed database recipes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from setup_system.layout import resource_root
from setup_system.settings import Settings
from setup_system.storage.engine import create_engine, prepare_file

VERSION_TABLE = "setup_system_v10_revision"


def verify_identity(connection: Connection) -> None:
    inspector = inspect(connection)
    objects = set(inspector.get_table_names()) | set(inspector.get_view_names())
    if objects and VERSION_TABLE not in objects:
        raise ValueError(
            "Incompatible database. Keep the existing file and select new v10 storage."
        )
    if VERSION_TABLE in objects and len(objects) > 1:
        revisions = list(
            connection.exec_driver_sql(
                "SELECT version_num FROM setup_system_v10_revision"
            ).scalars()
        )
        if len(revisions) != 1 or not isinstance(revisions[0], str):
            raise ValueError("The existing database has no valid v10 migration identity")


async def migrate_connection(engine: AsyncEngine, action: Callable[[Connection], None]) -> None:
    await asyncio.to_thread(prepare_file, engine)
    # SQLite serializes migration runs across processes and rolls DDL back on failure.
    async with engine.connect() as connection:
        await connection.execution_options(setup_system_write=True)
        async with connection.begin():
            await connection.run_sync(action)


def _config(connection: Connection) -> Config:
    cfg = Config(str(resource_root() / "alembic.ini"))
    cfg.attributes["connection"] = connection
    return cfg


async def upgrade(engine: AsyncEngine) -> None:
    """Bring a new or recognized database to head before opening repositories."""
    await migrate_connection(
        engine, lambda connection: command.upgrade(_config(connection), "head")
    )


async def _command(action: Callable[[Config], None]) -> None:
    engine = create_engine(Settings())
    try:
        await migrate_connection(engine, lambda connection: action(_config(connection)))
    finally:
        await engine.dispose()


cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@cli.command("upgrade")
def upgrade_command() -> None:
    asyncio.run(_command(lambda cfg: command.upgrade(cfg, "head")))


@cli.command()
def current() -> None:
    asyncio.run(_command(command.current))


@cli.command()
def check() -> None:
    asyncio.run(_command(command.check))


@cli.command("downgrade-empty")
def downgrade_empty() -> None:
    """Remove the schema from an empty v10 development database."""
    asyncio.run(_command(lambda cfg: command.downgrade(cfg, "base")))


@cli.command()
def revision() -> None:
    if not (resource_root() / ".git").exists():
        raise typer.BadParameter("Revision generation requires a development checkout")

    def generate(cfg: Config) -> None:
        command.revision(cfg, message="schema change", autogenerate=True)

    asyncio.run(_command(generate))


if __name__ == "__main__":
    cli()
