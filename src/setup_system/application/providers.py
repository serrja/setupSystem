"""Allow only declared read and planning calls to resolved provider workers."""

from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.manifests import Action
from setup_system_sdk.operations import StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.wire import Request

from setup_system.infrastructure.plugins.environments import Activation
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient


class ProviderBroker:
    def __init__(self, registry: PluginRegistry, workers: WorkerClient) -> None:
        self.registry = registry
        self.workers = workers

    def resolve(self, action: Action) -> dict[str, Activation]:
        return {r.id: self.registry.provider(r) for r in action.requires}

    def route(
        self,
        operation_id: UUID,
        providers: dict[str, Activation],
        approved: list[StepPlan],
        holdings: tuple[Resource, ...],
    ) -> Callable[[Literal["inspect", "plan"], CapabilityRequest], Awaitable[Values | StepPlan]]:
        async def dispatch(
            method: Literal["inspect", "plan"], request: CapabilityRequest
        ) -> Values | StepPlan:
            release = providers.get(request.capability)
            if not release:
                raise ValueError("The action did not declare this capability")
            reply = await self.workers.call(
                release,
                Request(
                    plugin_id=release.manifest.id,
                    operation_id=operation_id,
                    capability_request=request,
                    holdings=holdings,
                    method=WorkerMethod.PROVIDER_PLAN
                    if method == "plan"
                    else WorkerMethod.PROVIDER_INSPECT,
                ),
            )
            if method == "inspect" and reply.facts is not None:
                return reply.facts
            step = reply.step
            version = next(
                c.version for c in release.manifest.provides if c.id == request.capability
            )
            if (
                method != "plan"
                or not step
                or step.executor != release.executor
                or step.step_key != request.step_key
                or step.capability != request.capability
                or step.contract_version != version
                or step.permissions - set(release.manifest.permissions)
            ):
                raise ValueError("Provider returned an unapproved step identity or permissions")
            approved.append(step)
            return step

        return dispatch
