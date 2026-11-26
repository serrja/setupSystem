"""Operational evidence stored in the user database."""

from enum import StrEnum

from setup_system_sdk.enums.operation import Outcome
from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from setup_system.domain.operations import ChangePhase, OperationPhase, RollbackPhase, StepPhase


def stored_enum[E: StrEnum](enum: type[E]) -> SqlEnum:
    return SqlEnum(
        enum,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Base(DeclarativeBase):
    pass


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=True)
    plugin: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    release: Mapped[str] = mapped_column(Text)
    phase: Mapped[OperationPhase] = mapped_column(stored_enum(OperationPhase))
    outcome: Mapped[Outcome | None] = mapped_column(stored_enum(Outcome))
    started: Mapped[str | None] = mapped_column(Text, server_default=func.current_timestamp())
    historical: Mapped[bool] = mapped_column(server_default=text("0"))
    __table_args__ = (
        Index(
            "unfinished_action",
            "plugin",
            "action",
            unique=True,
            sqlite_where=text("historical = 0 AND phase IN ('prepared','applied','unresolved')"),
        ),
    )


class Confirmation(Base):
    __tablename__ = "confirmations"
    operation: Mapped[str] = mapped_column(
        Text, ForeignKey("operations.id"), primary_key=True, nullable=True
    )
    snapshot: Mapped[str] = mapped_column(Text)
    recovery: Mapped[str | None] = mapped_column(Text)
    historical_resources: Mapped[str | None] = mapped_column(Text)
    reconciliation: Mapped[str | None] = mapped_column(Text)


class Change(Base):
    __tablename__ = "changes"
    operation: Mapped[str] = mapped_column(Text, ForeignKey("operations.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    phase: Mapped[ChangePhase] = mapped_column(stored_enum(ChangePhase))
    resource: Mapped[str] = mapped_column(Text)


class LocalPublication(Base):
    __tablename__ = "local_publications"
    operation: Mapped[str] = mapped_column(
        Text, ForeignKey("operations.id"), primary_key=True, nullable=True
    )


class NativeReadback(Base):
    __tablename__ = "native_readbacks"
    operation: Mapped[str] = mapped_column(Text, ForeignKey("operations.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    resource: Mapped[str] = mapped_column(Text)


class Holding(Base):
    __tablename__ = "holdings"
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    plugin: Mapped[str] = mapped_column(Text, primary_key=True)
    resource: Mapped[str] = mapped_column(Text)


class Requirement(Base):
    __tablename__ = "requirements"
    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)


class PlannedRequirement(Base):
    __tablename__ = "planned_requirements"
    operation: Mapped[str] = mapped_column(
        Text, ForeignKey("operations.id"), primary_key=True, nullable=True
    )
    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, primary_key=True)


class Rollback(Base):
    __tablename__ = "rollbacks"
    operation: Mapped[str] = mapped_column(
        Text, ForeignKey("operations.id"), primary_key=True, nullable=True
    )
    phase: Mapped[RollbackPhase] = mapped_column(stored_enum(RollbackPhase))


class Reservation(Base):
    __tablename__ = "reservations"
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    operation: Mapped[str] = mapped_column(Text, ForeignKey("operations.id"))


class OperationStep(Base):
    __tablename__ = "operation_steps"
    operation: Mapped[str] = mapped_column(Text, ForeignKey("operations.id"), primary_key=True)
    step_key: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int]
    phase: Mapped[StepPhase] = mapped_column(stored_enum(StepPhase))
    receipt: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("operation", "position"),
        CheckConstraint("position >= 0"),
        CheckConstraint("phase IN ('prepared','running','applied','verified','unresolved')"),
    )
