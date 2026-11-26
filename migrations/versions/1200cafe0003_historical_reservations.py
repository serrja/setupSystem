"""Retain conflicting historical operations for individual reconciliation."""

import sqlalchemy as sa
from alembic import op

revision = "1200cafe0003"
down_revision = "1200cafe0002"


def upgrade() -> None:
    op.add_column(
        "operations",
        sa.Column("historical", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("confirmations", sa.Column("historical_resources", sa.Text()))
    op.add_column("confirmations", sa.Column("reconciliation", sa.Text()))
    op.drop_index("unfinished_action", table_name="operations")
    op.create_index(
        "unfinished_action",
        "operations",
        ["plugin", "action"],
        unique=True,
        sqlite_where=sa.text("historical = 0 AND phase IN ('prepared','applied','unresolved')"),
    )
    # A cancelled, never-started attempt proves its reservation can be retired.
    op.execute("""
        DELETE FROM rollbacks WHERE phase='unresolved'
          AND EXISTS (
            SELECT 1 FROM confirmations c JOIN operations o ON o.id=c.operation
            WHERE json_valid(c.snapshot)
              AND json_extract(c.snapshot,'$.rollback_of')=rollbacks.operation
              AND o.phase='verified' AND o.outcome='cancelled'
              AND EXISTS (SELECT 1 FROM operation_steps WHERE operation=o.id)
              AND NOT EXISTS (SELECT 1 FROM operation_steps WHERE operation=o.id
                              AND (phase!='prepared' OR receipt IS NOT NULL))
          ) AND NOT EXISTS (
            SELECT 1 FROM confirmations c JOIN operations o ON o.id=c.operation
            WHERE json_valid(c.snapshot)
              AND json_extract(c.snapshot,'$.rollback_of')=rollbacks.operation
              AND (o.phase!='verified' OR o.outcome IS NOT 'cancelled')
          )
    """)


def downgrade() -> None:
    raise ValueError("Use the pre-migration backup to restore historical reservations")
