"""Record execution evidence per approved step."""

import sqlalchemy as sa
from alembic import op

revision = "1200cafe0002"
down_revision = "1200cafe0001"


def upgrade() -> None:
    op.create_table(
        "operation_steps",
        sa.Column("operation", sa.Text(), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("receipt", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("operation", "step_key"),
        sa.UniqueConstraint("operation", "position"),
        sa.CheckConstraint("position >= 0"),
        sa.CheckConstraint("phase IN ('prepared','running','applied','verified','unresolved')"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM operation_steps LIMIT 1")):
        raise ValueError("Downgrade would discard execution evidence")
    op.drop_table("operation_steps")
