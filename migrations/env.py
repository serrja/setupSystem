"""Run migrations on the connection protected by the host schema lease."""

from alembic import context
from sqlalchemy import MetaData
from sqlalchemy.engine import Connection

from setup_system.infrastructure.database.models import Base

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("Run setup-system migrate as the database owner")
metadata = Base.metadata
if context.config.attributes.get("version_table") == "setup_system_v10_revision":
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
context.configure(
    connection=connection,
    target_metadata=metadata,
    transactional_ddl=True,
    version_table=context.config.attributes.get("version_table", "alembic_version"),
)
with context.begin_transaction():
    context.run_migrations()
