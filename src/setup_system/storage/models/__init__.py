"""Mapped tables in the v10 operational store."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from setup_system.storage.base import Base, enum_column
from setup_system_sdk.enums.operation import EventLevel, EventPhase, OperationState, Outcome
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership


class Extension(Base):
    __tablename__ = "extensions"
    plugin_id: Mapped[str] = mapped_column(primary_key=True)


class Release(Base):
    __tablename__ = "extension_releases"
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[str] = mapped_column(ForeignKey("extensions.plugin_id"))
    digest: Mapped[str]
    version: Mapped[str]
    source: Mapped[str]
    active: Mapped[bool] = mapped_column(default=False)
    installed: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (
        UniqueConstraint("plugin_id", "digest"),
        CheckConstraint("length(digest) = 64", name="digest_length"),
        CheckConstraint("NOT active OR installed", name="active_installed"),
    )


Index(
    "uq_active_extension_release",
    Release.plugin_id,
    unique=True,
    sqlite_where=Release.active.is_(True),
)


class Operation(Base):
    __tablename__ = "operations"
    seq: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("extension_releases.id"), index=True)
    action_id: Mapped[str]
    status: Mapped[OperationState] = mapped_column(enum_column(OperationState))
    outcome: Mapped[Outcome | None] = mapped_column(enum_column(Outcome))
    settings_revision: Mapped[str]
    inputs: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON)
    plan_summary: Mapped[str]
    plan_outcome: Mapped[Outcome] = mapped_column(enum_column(Outcome, name="plan_outcome"))
    receipt: Mapped[str | None] = mapped_column(unique=True)
    started_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    __table_args__ = (
        Index("ix_operation_status_started", "status", "started_at"),
        CheckConstraint("length(action_id) BETWEEN 1 AND 80", name="action_length"),
    )


class PlanWarning(Base):
    __tablename__ = "plan_warnings"
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    message: Mapped[str]


class ActiveOperation(Base):
    """Keep even zero-resource mutations and unknown outcomes mutually exclusive."""

    __tablename__ = "active_operations"
    plugin_id: Mapped[str] = mapped_column(ForeignKey("extensions.plugin_id"), primary_key=True)
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), unique=True)


class ResourceKey(Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    identity: Mapped[str]
    __table_args__ = (UniqueConstraint("kind", "identity"),)


class Observation(Base):
    """An immutable resource declaration or verified observation at one point in time."""

    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource: Mapped[int] = mapped_column(ForeignKey("resources.id"), index=True)
    verified: Mapped[bool]
    ownership: Mapped[ResourceOwnership] = mapped_column(enum_column(ResourceOwnership))
    effect: Mapped[ResourceEffect] = mapped_column(enum_column(ResourceEffect))
    cleanup: Mapped[bool]
    before: Mapped[str | None]
    after: Mapped[str | None]
    before_permissions: Mapped[int | None]
    permissions: Mapped[int | None]
    remote_id: Mapped[str | None]
    source_sha256: Mapped[str | None]
    __table_args__ = (
        CheckConstraint("permissions BETWEEN 0 AND 4095", name="mode"),
        CheckConstraint("before_permissions BETWEEN 0 AND 4095", name="previous_mode"),
    )


class Consumer(Base):
    position: Mapped[int]
    __tablename__ = "resource_consumers"
    observation: Mapped[int] = mapped_column(ForeignKey("observations.id"), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(primary_key=True)


class Holding(Base):
    __tablename__ = "ownership"
    resource: Mapped[int] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("extensions.plugin_id"), primary_key=True, index=True
    )
    observation: Mapped[int] = mapped_column(ForeignKey("observations.id"))


class PreviousHolding(Base):
    __tablename__ = "previous_ownership"
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), primary_key=True)
    resource: Mapped[int] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    observation: Mapped[int] = mapped_column(ForeignKey("observations.id"))


class Change(Base):
    __tablename__ = "resource_changes"
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), primary_key=True)
    resource: Mapped[int] = mapped_column(ForeignKey("resources.id"), primary_key=True, index=True)
    position: Mapped[int]
    proposed: Mapped[int] = mapped_column(ForeignKey("observations.id"))
    native: Mapped[int | None] = mapped_column(ForeignKey("observations.id"))
    confirmed: Mapped[int | None] = mapped_column(ForeignKey("observations.id"))
    __table_args__ = (UniqueConstraint("operation", "position"),)


class Reservation(Base):
    __tablename__ = "resource_reservations"
    resource: Mapped[int] = mapped_column(primary_key=True)
    operation: Mapped[int] = mapped_column(index=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation", "resource"], ["resource_changes.operation", "resource_changes.resource"]
        ),
    )


class Dependency(Base):
    """A reservation on a provider becomes durable on confirmed installation."""

    __tablename__ = "program_dependencies"
    consumer: Mapped[str] = mapped_column(ForeignKey("extensions.plugin_id"), primary_key=True)
    provider: Mapped[str] = mapped_column(primary_key=True, index=True)
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), primary_key=True)
    confirmed: Mapped[bool] = mapped_column(default=False)


class JournalEvent(Base):
    __tablename__ = "journal_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), index=True)
    status: Mapped[OperationState] = mapped_column(enum_column(OperationState))
    outcome: Mapped[Outcome | None] = mapped_column(enum_column(Outcome))
    level: Mapped[EventLevel] = mapped_column(enum_column(EventLevel))
    phase: Mapped[EventPhase] = mapped_column(enum_column(EventPhase))
    message_code: Mapped[str]
    resource_count: Mapped[int | None]
    timestamp: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    __table_args__ = (CheckConstraint("resource_count >= 0", name="resource_count"),)


class Restore(Base):
    __tablename__ = "restores"
    operation: Mapped[int] = mapped_column(ForeignKey("operations.seq"), primary_key=True)
    complete: Mapped[bool] = mapped_column(default=False)
