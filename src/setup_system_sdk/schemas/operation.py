from typing import Literal

from pydantic import Field

from setup_system_sdk.enums.operation import EventLevel, EventPhase, Outcome
from setup_system_sdk.enums.resources import ResourceEffect, ResourceOwnership
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs, Values


class Resource(Inputs):
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    kind: Identifier
    identity: str = Field(min_length=1, max_length=4096)
    verified: bool = False
    remote_id: str | None = Field(default=None, max_length=512)
    ownership: ResourceOwnership
    before: str | None = None
    after: str | None = None
    cleanup: bool = False
    effect: ResourceEffect = ResourceEffect.ENSURE
    before_permissions: int | None = Field(default=None, ge=0, le=0o7777)
    permissions: int | None = Field(default=None, ge=0, le=0o7777)
    consumers: tuple[Identifier, ...] = ()

    def description(self) -> str:
        """Show exact approved versions, remote objects and permissions in consent."""
        values = [f"{self.effect} {self.kind} · {self.identity} · {self.ownership}"]
        for label, value in (
            ("Before", self.before),
            ("After", self.after),
            ("Source SHA-256", self.source_sha256),
            ("Remote ID", self.remote_id),
        ):
            if value is not None:
                values.append(f"{label}: {value}")
        for label, mode in (
            ("Previous permissions", self.before_permissions),
            ("Permissions", self.permissions),
        ):
            if mode is not None:
                values.append(f"{label}: {mode:04o}")
        if self.consumers:
            values.append("Consumers: " + ", ".join(self.consumers))
        return " · ".join(
            "".join(c if c.isprintable() else repr(c)[1:-1] for c in v) for v in values
        )


class Plan(Inputs):
    summary: str = Field(max_length=8192)
    resources: list[Resource] = Field(default_factory=list, max_length=4096)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    outcome: Outcome = Outcome.READY


class ActionEvent(Inputs):
    """A worker observation. The host binds its operation, action and plugin identity."""

    level: EventLevel = EventLevel.INFO
    phase: EventPhase
    message_code: Identifier
    outcome: Outcome | None = None
    details: dict[Literal["resource_count"], int] = Field(default_factory=dict)


class Result(Inputs):
    events: tuple[ActionEvent, ...] = Field(default=(), max_length=64)
    recovery_id: str | None = Field(default=None, pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
    outcome: Outcome
    message: str = Field(max_length=8192)
    resources: list[Resource] = Field(default_factory=list, max_length=4096)

    @classmethod
    def observed(cls, *, outcome: Outcome, message: str, resources: list[Resource]) -> "Result":
        """Report a set of resources checked together by a native readback handler."""
        if outcome in (Outcome.COMPLETE, Outcome.CHANGED, Outcome.NO_CHANGES, Outcome.NEEDS_REBOOT):
            resources = [row.model_copy(update={"verified": True}) for row in resources]
        return cls(outcome=outcome, message=message, resources=resources)


class Mutation(Inputs):
    holdings: tuple[Resource, ...] = ()
    action_id: ActionId
    settings: Values
    inputs: Values
    plan: Plan
