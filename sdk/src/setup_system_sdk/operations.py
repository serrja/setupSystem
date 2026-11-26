"""Immutable plans, confirmed execution identities and evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self
from uuid import UUID

from packaging.version import Version
from pydantic import Field, field_validator, model_validator

from setup_system_sdk.enums.operation import EventLevel, EventPhase, Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ResourceEffect
from setup_system_sdk.files.manifest_types import Digest
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs, Values


class Executor(Inputs):
    plugin_id: Identifier
    version: str
    release_digest: Digest
    generation: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        Version(value)
        return value


class StepPlan(Inputs):
    step_key: Identifier
    executor: Executor
    handler: ActionId
    capability: ActionId | None = None
    contract_version: str
    payload: Values = Field(default_factory=dict)
    preconditions: Values = Field(default_factory=dict)
    resources: tuple[Resource, ...] = Field(default=(), max_length=4096)
    permissions: frozenset[Permission] = frozenset()
    lock_keys: tuple[ActionId, ...] = Field(default=(), max_length=128)
    cancellation: Literal["terminate", "safe_boundary"] = "terminate"


class Plan(Inputs):
    steps: tuple[StepPlan, ...] = Field(default=(), max_length=256)

    summary: str = Field(max_length=8192)
    resources: list[Resource] = Field(default_factory=list, max_length=4096)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    outcome: Outcome = Outcome.READY

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        if len({s.step_key for s in self.steps}) != len(self.steps):
            raise ValueError("Step keys must be unique")
        keys = [(r.kind, r.identity) for r in self.resources]
        if len(keys) != len(set(keys)):
            raise ValueError("A plan repeats a resource")
        if self.steps:
            rows = [r for step in self.steps for r in step.resources]
            if len(rows) != len(keys) or {(r.kind, r.identity): r for r in rows} != dict(
                zip(keys, self.resources, strict=True)
            ):
                raise ValueError("Step resources must partition the approved plan")
        return self

    def digest(self) -> str:
        values = self.model_dump(mode="json")
        for step in values["steps"]:
            step["permissions"].sort()
            step["lock_keys"].sort()
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


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
    def observed(cls, *, outcome: Outcome, message: str, resources: list[Resource]) -> Result:
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


class Snapshot(Inputs):
    revision: Literal[3] = 3
    operation_id: UUID
    executor: Executor
    action_id: ActionId
    settings_revision: str
    rollback_of: UUID | None = None
    overrides: Values = Field(default_factory=dict, repr=False)
    settings: Values = Field(repr=False)
    inputs: Values = Field(repr=False)
    plan: Plan
    dependencies: tuple[Identifier, ...] = ()
    holdings: tuple[Resource, ...] = ()

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.plan.resources and not self.plan.steps:
            raise ValueError("A confirmed resource requires a step owner")
        return self


class Receipt(Inputs):
    revision: Literal[3] = 3
    operation_id: UUID
    step_key: Identifier
    executor: Executor
    result: Result

    def validate_for(self, operation_id: UUID, step: StepPlan) -> None:
        if (self.operation_id, self.step_key, self.executor) != (
            operation_id,
            step.step_key,
            step.executor,
        ):
            raise ValueError("Receipt does not identify the confirmed step")
        expected = {(r.kind, r.identity): r for r in step.resources}
        seen: set[tuple[str, str]] = set()
        for resource in self.result.resources:
            key = resource.kind, resource.identity
            approved = expected.get(key)
            if approved is None or key in seen:
                raise ValueError("Receipt exceeds the approved resources")
            observed = resource.model_copy(update={"verified": approved.verified})
            comparison = approved.model_copy(
                update={
                    "after": observed.after
                    if approved.after is None and approved.effect != ResourceEffect.REMOVE
                    else approved.after,
                    "permissions": observed.permissions
                    if approved.permissions is None
                    else approved.permissions,
                    "remote_id": observed.remote_id
                    if approved.remote_id is None
                    else approved.remote_id,
                }
            )
            if observed != comparison:
                raise ValueError("Receipt changed the approved resource")
            seen.add(key)
