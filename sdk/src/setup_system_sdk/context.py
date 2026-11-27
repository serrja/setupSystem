"""A request-scoped context with explicitly granted provider operations."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import anyio

from setup_system_sdk.enums.operation import ProviderMethod
from setup_system_sdk.manifests import Action
from setup_system_sdk.operations import ActionEvent, Executor, Receipt, StepPlan
from setup_system_sdk.processes import ExecutionProcesses
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Values

type ProviderCall = Callable[[ProviderMethod, CapabilityRequest], Awaitable[Values | StepPlan]]


@dataclass(frozen=True)
class PluginContext:
    operation_id: UUID
    executor: Executor
    cancelled: anyio.Event
    emit: Callable[[ActionEvent], Awaitable[None]]
    provider_call: ProviderCall | None = None

    action: Action | None = None

    native_call: Callable[[], Awaitable[Receipt]] | None = None

    processes: ExecutionProcesses | None = None

    async def execute_native(self) -> Receipt:
        if self.native_call is None:
            raise ValueError("No native execution grant for this dispatch")
        if self.cancelled.is_set():
            raise ValueError("Authorization stopped before native execution")
        return await self.native_call()

    async def inspect_provider(self, request: CapabilityRequest) -> Values:
        if self.provider_call is None:
            raise ValueError("Provider calls are unavailable in this context")
        response = await self.provider_call(ProviderMethod.INSPECT, request)
        if not isinstance(response, dict):
            raise ValueError("Expected provider facts")
        return response

    async def plan_provider(self, request: CapabilityRequest) -> StepPlan:
        if self.provider_call is None:
            raise ValueError("Provider calls are unavailable in this context")
        response = await self.provider_call(ProviderMethod.PLAN, request)
        if not isinstance(response, StepPlan):
            raise ValueError("Expected a provider step")
        return response
