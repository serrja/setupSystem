"""Versioned messages on private pipes. Plugin imports occur only in its worker."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.operations import Executor, Receipt, Snapshot, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs, Values
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.operation import Plan, Resource, Result

LIMIT = 512 * 1024


class Request(Inputs):
    protocol: Literal[2] = 2
    operation_id: UUID | None = None
    plugin_id: Identifier
    executor: Executor | None = None
    snapshot: Snapshot | None = None
    step_key: Identifier | None = None
    receipt: Receipt | None = None
    capability_request: CapabilityRequest | None = None
    method: WorkerMethod
    action_id: ActionId | None = None
    holdings: tuple[Resource, ...] = ()
    saved: Values = Field(default_factory=dict, repr=False)
    overrides: Values = Field(default_factory=dict, repr=False)
    inputs: Values = Field(default_factory=dict, repr=False)
    plan: Plan | None = None
    settings_version: int = Field(default=1, ge=1)
    provider: Identifier | None = None
    query: str = Field(default="", max_length=256)


class Reply(Inputs):
    facts: Values | None = None
    step: StepPlan | None = None
    receipt: Receipt | None = None
    error: Error | None = None
    protocol: Literal[2] = 2
    result: Result | None = None
    plan: Plan | None = None
    choices: tuple[str, ...] = Field(default=(), max_length=128)
    settings: Values = Field(default_factory=dict, repr=False)
