"""Capability messages and the handler contract implemented by their owner."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol

from setup_system_sdk.operations import Receipt, Result, StepPlan
from setup_system_sdk.schemas.common import ActionId, Identifier, Inputs, Values

if TYPE_CHECKING:
    from setup_system_sdk.context import PluginContext


class CapabilityRequest(Inputs):
    capability: ActionId
    step_key: Identifier
    payload: Values


class Provider(Protocol):
    request_model: ClassVar[type[Inputs]]

    async def inspect(self, request: CapabilityRequest, context: PluginContext) -> Values: ...
    async def plan(self, request: CapabilityRequest, context: PluginContext) -> StepPlan: ...
    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt: ...
    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result: ...
