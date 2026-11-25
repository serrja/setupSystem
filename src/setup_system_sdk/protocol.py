"""Versioned messages on private pipes. Plugin imports occur only in its worker."""

from typing import Literal

from pydantic import Field

from setup_system_sdk import ActionId, Identifier, Inputs, Plan, Resource, Result, Values

LIMIT = 512 * 1024


class Request(Inputs):
    protocol: Literal[1] = 1
    plugin_id: Identifier
    method: Literal["inspect", "plan", "verify", "choices", "validate", "migrate"]
    action_id: ActionId | None = None
    holdings: tuple[Resource, ...] = ()
    saved: Values = Field(default_factory=dict, repr=False)
    profile: Values = Field(default_factory=dict, repr=False)
    overrides: Values = Field(default_factory=dict, repr=False)
    inputs: Values = Field(default_factory=dict, repr=False)
    plan: Plan | None = None
    settings_version: int = Field(default=1, ge=1)
    provider: Identifier | None = None
    query: str = Field(default="", max_length=256)


class Reply(Inputs):
    protocol: Literal[1] = 1
    result: Result | None = None
    plan: Plan | None = None
    choices: tuple[str, ...] = Field(default=(), max_length=128)
    settings: Values = Field(default_factory=dict, repr=False)
