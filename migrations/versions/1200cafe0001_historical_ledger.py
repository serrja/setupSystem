"""Establish the historical ten-table ownership ledger."""

from alembic import op

revision = "1200cafe0001"
down_revision = None


SCHEMA = """
CREATE TABLE operations(
    id TEXT PRIMARY KEY, plugin TEXT NOT NULL, action TEXT NOT NULL, release TEXT NOT NULL,
    phase TEXT NOT NULL, outcome TEXT, started TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX unfinished_action ON operations(plugin,action)
WHERE phase IN ('prepared','applied','unresolved');
CREATE TABLE confirmations(
    operation TEXT PRIMARY KEY REFERENCES operations(id), snapshot TEXT NOT NULL, recovery TEXT
);
CREATE TABLE changes(
    operation TEXT NOT NULL REFERENCES operations(id), kind TEXT NOT NULL, identity TEXT NOT NULL,
    phase TEXT NOT NULL, resource TEXT NOT NULL, PRIMARY KEY(operation, kind, identity)
);
CREATE TABLE local_publications(operation TEXT PRIMARY KEY REFERENCES operations(id));
CREATE TABLE native_readbacks(
    operation TEXT NOT NULL REFERENCES operations(id), kind TEXT NOT NULL, identity TEXT NOT NULL,
    resource TEXT NOT NULL, PRIMARY KEY(operation,kind,identity)
);
CREATE TABLE holdings(
    kind TEXT NOT NULL, identity TEXT NOT NULL, plugin TEXT NOT NULL, resource TEXT NOT NULL,
    PRIMARY KEY(kind, identity, plugin)
);
CREATE TABLE requirements(consumer TEXT NOT NULL, provider TEXT NOT NULL,
    PRIMARY KEY(consumer,provider));
CREATE TABLE planned_requirements(
    operation TEXT REFERENCES operations(id), consumer TEXT NOT NULL,provider TEXT NOT NULL,
    PRIMARY KEY(operation,consumer,provider)
);
CREATE TABLE rollbacks(
    operation TEXT PRIMARY KEY REFERENCES operations(id), phase TEXT NOT NULL
);
CREATE TABLE reservations(
    kind TEXT NOT NULL, identity TEXT NOT NULL, operation TEXT NOT NULL REFERENCES operations(id),
    PRIMARY KEY(kind, identity)
);
"""


def upgrade() -> None:
    for statement in SCHEMA.strip().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise ValueError("Use the pre-migration backup to restore the ownership ledger")
