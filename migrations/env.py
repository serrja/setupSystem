"""Attach the current SQLAlchemy connection to the versioned v10 metadata."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from setup_system.settings import Settings
from setup_system.storage import models  # noqa: F401
from setup_system.storage.base import Base
from setup_system.storage.engine import create_engine
from setup_system.storage.migrations import VERSION_TABLE, migrate_connection, verify_identity


def run_migrations(connection: Connection) -> None:
    verify_identity(connection)
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table=VERSION_TABLE,
        render_as_batch=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def standalone() -> None:
    engine = create_engine(Settings())
    try:
        await migrate_connection(engine, run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Select an isolated online SQLite database to verify schema identity")
shared = context.config.attributes.get("connection")
if isinstance(shared, Connection):
    run_migrations(shared)
else:
    asyncio.run(standalone())
