"""Establish the v10 operational store."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1000a10c0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extensions",
        sa.Column("plugin_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("plugin_id", name=op.f("pk_extensions")),
    )
    op.create_table(
        "resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("identity", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
        sa.UniqueConstraint("kind", "identity", name=op.f("uq_resources_kind_identity")),
    )
    op.create_table(
        "extension_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plugin_id", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("installed", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "NOT active OR installed", name=op.f("ck_extension_releases_active_installed")
        ),
        sa.CheckConstraint("length(digest) = 64", name=op.f("ck_extension_releases_digest_length")),
        sa.ForeignKeyConstraint(
            ["plugin_id"],
            ["extensions.plugin_id"],
            name=op.f("fk_extension_releases_plugin_id_extensions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extension_releases")),
        sa.UniqueConstraint(
            "plugin_id", "digest", name=op.f("uq_extension_releases_plugin_id_digest")
        ),
    )
    with op.batch_alter_table("extension_releases", schema=None) as batch_op:
        batch_op.create_index(
            "uq_active_extension_release",
            ["plugin_id"],
            unique=True,
            sqlite_where=sa.text("active IS 1"),
        )

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resource", sa.Integer(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column(
            "ownership",
            sa.Enum(
                "created",
                "modified",
                "preexisting",
                "shared",
                name="resourceownership",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "effect",
            sa.Enum(
                "ensure", "remove", name="resourceeffect", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("cleanup", sa.Boolean(), nullable=False),
        sa.Column("before", sa.String(), nullable=True),
        sa.Column("after", sa.String(), nullable=True),
        sa.Column("before_permissions", sa.Integer(), nullable=True),
        sa.Column("permissions", sa.Integer(), nullable=True),
        sa.Column("remote_id", sa.String(), nullable=True),
        sa.Column("source_sha256", sa.String(), nullable=True),
        sa.CheckConstraint(
            "before_permissions BETWEEN 0 AND 4095", name=op.f("ck_observations_previous_mode")
        ),
        sa.CheckConstraint("permissions BETWEEN 0 AND 4095", name=op.f("ck_observations_mode")),
        sa.ForeignKeyConstraint(
            ["resource"], ["resources.id"], name=op.f("fk_observations_resource_resources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observations")),
    )
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_observations_resource"), ["resource"], unique=False)

    op.create_table(
        "operations",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "unknown",
                "idle",
                "validating",
                "planning",
                "ready",
                "blocked",
                "authenticating",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                name="operationstate",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "no_changes",
                "ready",
                "changed",
                "complete",
                "needs_user_action",
                "needs_reboot",
                "cancelled",
                "partial",
                "blocked",
                "failed",
                "unknown_outcome",
                name="outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("settings_revision", sa.String(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("plan_summary", sa.String(), nullable=False),
        sa.Column(
            "plan_outcome",
            sa.Enum(
                "no_changes",
                "ready",
                "changed",
                "complete",
                "needs_user_action",
                "needs_reboot",
                "cancelled",
                "partial",
                "blocked",
                "failed",
                "unknown_outcome",
                name="plan_outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("receipt", sa.String(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(action_id) BETWEEN 1 AND 80", name=op.f("ck_operations_action_length")
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["extension_releases.id"],
            name=op.f("fk_operations_release_id_extension_releases"),
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_operations")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_operations_operation_id")),
        sa.UniqueConstraint("receipt", name=op.f("uq_operations_receipt")),
    )
    with op.batch_alter_table("operations", schema=None) as batch_op:
        batch_op.create_index("ix_operation_status_started", ["status", "started_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_operations_release_id"), ["release_id"], unique=False)

    op.create_table(
        "ownership",
        sa.Column("resource", sa.Integer(), nullable=False),
        sa.Column("plugin_id", sa.String(), nullable=False),
        sa.Column("observation", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation"], ["observations.id"], name=op.f("fk_ownership_observation_observations")
        ),
        sa.ForeignKeyConstraint(
            ["plugin_id"], ["extensions.plugin_id"], name=op.f("fk_ownership_plugin_id_extensions")
        ),
        sa.ForeignKeyConstraint(
            ["resource"], ["resources.id"], name=op.f("fk_ownership_resource_resources")
        ),
        sa.PrimaryKeyConstraint("resource", "plugin_id", name=op.f("pk_ownership")),
    )
    with op.batch_alter_table("ownership", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ownership_plugin_id"), ["plugin_id"], unique=False)

    op.create_table(
        "resource_consumers",
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("observation", sa.Integer(), nullable=False),
        sa.Column("plugin_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation"],
            ["observations.id"],
            name=op.f("fk_resource_consumers_observation_observations"),
        ),
        sa.PrimaryKeyConstraint("observation", "plugin_id", name=op.f("pk_resource_consumers")),
    )
    op.create_table(
        "active_operations",
        sa.Column("plugin_id", sa.String(), nullable=False),
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation"],
            ["operations.seq"],
            name=op.f("fk_active_operations_operation_operations"),
        ),
        sa.ForeignKeyConstraint(
            ["plugin_id"],
            ["extensions.plugin_id"],
            name=op.f("fk_active_operations_plugin_id_extensions"),
        ),
        sa.PrimaryKeyConstraint("plugin_id", name=op.f("pk_active_operations")),
        sa.UniqueConstraint("operation", name=op.f("uq_active_operations_operation")),
    )
    op.create_table(
        "journal_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "unknown",
                "idle",
                "validating",
                "planning",
                "ready",
                "blocked",
                "authenticating",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                name="operationstate",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "no_changes",
                "ready",
                "changed",
                "complete",
                "needs_user_action",
                "needs_reboot",
                "cancelled",
                "partial",
                "blocked",
                "failed",
                "unknown_outcome",
                name="outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "level",
            sa.Enum(
                "info",
                "warning",
                "error",
                name="eventlevel",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "phase",
            sa.Enum(
                "planned",
                "prepared",
                "applied",
                "verified",
                "unresolved",
                "blocked",
                name="eventphase",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("message_code", sa.String(), nullable=False),
        sa.Column("resource_count", sa.Integer(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("resource_count >= 0", name=op.f("ck_journal_events_resource_count")),
        sa.ForeignKeyConstraint(
            ["operation"], ["operations.seq"], name=op.f("fk_journal_events_operation_operations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_events")),
    )
    with op.batch_alter_table("journal_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_journal_events_operation"), ["operation"], unique=False
        )

    op.create_table(
        "plan_warnings",
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation"], ["operations.seq"], name=op.f("fk_plan_warnings_operation_operations")
        ),
        sa.PrimaryKeyConstraint("operation", "position", name=op.f("pk_plan_warnings")),
    )
    op.create_table(
        "previous_ownership",
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.Column("resource", sa.Integer(), nullable=False),
        sa.Column("observation", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation"],
            ["observations.id"],
            name=op.f("fk_previous_ownership_observation_observations"),
        ),
        sa.ForeignKeyConstraint(
            ["operation"],
            ["operations.seq"],
            name=op.f("fk_previous_ownership_operation_operations"),
        ),
        sa.ForeignKeyConstraint(
            ["resource"], ["resources.id"], name=op.f("fk_previous_ownership_resource_resources")
        ),
        sa.PrimaryKeyConstraint("operation", "resource", name=op.f("pk_previous_ownership")),
    )
    op.create_table(
        "program_dependencies",
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["consumer"],
            ["extensions.plugin_id"],
            name=op.f("fk_program_dependencies_consumer_extensions"),
        ),
        sa.ForeignKeyConstraint(
            ["operation"],
            ["operations.seq"],
            name=op.f("fk_program_dependencies_operation_operations"),
        ),
        sa.PrimaryKeyConstraint(
            "consumer", "provider", "operation", name=op.f("pk_program_dependencies")
        ),
    )
    with op.batch_alter_table("program_dependencies", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_program_dependencies_provider"), ["provider"], unique=False
        )

    op.create_table(
        "resource_changes",
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.Column("resource", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("proposed", sa.Integer(), nullable=False),
        sa.Column("native", sa.Integer(), nullable=True),
        sa.Column("confirmed", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["confirmed"],
            ["observations.id"],
            name=op.f("fk_resource_changes_confirmed_observations"),
        ),
        sa.ForeignKeyConstraint(
            ["native"], ["observations.id"], name=op.f("fk_resource_changes_native_observations")
        ),
        sa.ForeignKeyConstraint(
            ["operation"], ["operations.seq"], name=op.f("fk_resource_changes_operation_operations")
        ),
        sa.ForeignKeyConstraint(
            ["proposed"],
            ["observations.id"],
            name=op.f("fk_resource_changes_proposed_observations"),
        ),
        sa.ForeignKeyConstraint(
            ["resource"], ["resources.id"], name=op.f("fk_resource_changes_resource_resources")
        ),
        sa.PrimaryKeyConstraint("operation", "resource", name=op.f("pk_resource_changes")),
        sa.UniqueConstraint(
            "operation", "position", name=op.f("uq_resource_changes_operation_position")
        ),
    )
    with op.batch_alter_table("resource_changes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_resource_changes_resource"), ["resource"], unique=False
        )

    op.create_table(
        "restores",
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation"], ["operations.seq"], name=op.f("fk_restores_operation_operations")
        ),
        sa.PrimaryKeyConstraint("operation", name=op.f("pk_restores")),
    )
    op.create_table(
        "resource_reservations",
        sa.Column("resource", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation", "resource"],
            ["resource_changes.operation", "resource_changes.resource"],
            name=op.f("fk_resource_reservations_operation_resource_resource_changes"),
        ),
        sa.PrimaryKeyConstraint("resource", name=op.f("pk_resource_reservations")),
    )
    with op.batch_alter_table("resource_reservations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_resource_reservations_operation"), ["operation"], unique=False
        )


def downgrade() -> None:
    # Never discard extension registration, receipts or conflicts with an initial downgrade.
    for table in ("extensions", "resources"):
        if op.get_bind().scalar(sa.select(sa.literal(1)).select_from(sa.table(table)).limit(1)):
            raise RuntimeError("Downgrade to base requires an empty operational store")
    with op.batch_alter_table("resource_reservations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resource_reservations_operation"))

    op.drop_table("resource_reservations")
    op.drop_table("restores")
    with op.batch_alter_table("resource_changes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_resource_changes_resource"))

    op.drop_table("resource_changes")
    with op.batch_alter_table("program_dependencies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_program_dependencies_provider"))

    op.drop_table("program_dependencies")
    op.drop_table("previous_ownership")
    op.drop_table("plan_warnings")
    with op.batch_alter_table("journal_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_journal_events_operation"))

    op.drop_table("journal_events")
    op.drop_table("active_operations")
    op.drop_table("resource_consumers")
    with op.batch_alter_table("ownership", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ownership_plugin_id"))

    op.drop_table("ownership")
    with op.batch_alter_table("operations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_operations_release_id"))
        batch_op.drop_index("ix_operation_status_started")

    op.drop_table("operations")
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_observations_resource"))

    op.drop_table("observations")
    with op.batch_alter_table("extension_releases", schema=None) as batch_op:
        batch_op.drop_index("uq_active_extension_release", sqlite_where=sa.text("active IS 1"))

    op.drop_table("extension_releases")
    op.drop_table("resources")
    op.drop_table("extensions")
