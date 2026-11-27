"""Allow only declared read and planning calls to resolved provider workers."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from setup_system_sdk.enums.operation import ProviderMethod, WorkerMethod
from setup_system_sdk.manifests import Action
from setup_system_sdk.operations import StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.wire import Request

from setup_system.domain.confirmation import ConfirmedSnapshot, ProviderBinding
from setup_system.infrastructure.plugins.environments import Activation
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient


class ProviderBroker:
    def __init__(self, registry: PluginRegistry, workers: WorkerClient) -> None:
        self.registry = registry
        self.workers = workers

    def resolve(self, action: Action) -> dict[str, Activation]:
        return {r.id: self.registry.provider(r) for r in action.requires}

    def retained(self, action: Action, snapshot: ConfirmedSnapshot) -> dict[str, Activation]:
        bindings = dict(snapshot.providers)
        for step in snapshot.plan.steps:
            if step.capability:
                planned = ProviderBinding(
                    executor=step.executor, contract_version=step.contract_version
                )
                if bindings.setdefault(step.capability, planned) != planned:
                    raise ValueError("Conflicting provider identities in the confirmed plan")
        releases = {}
        for requirement in action.requires:
            binding = bindings.get(requirement.id)
            if binding is None:
                raise ValueError(
                    f"No recorded executor for {requirement.id}. Review the original release."
                )
            release = self.registry.resolve_executor(binding.executor)
            if Version(binding.contract_version) not in SpecifierSet(
                requirement.version
            ) or not any(
                c.id == requirement.id and c.version == binding.contract_version
                for c in release.manifest.provides
            ):
                raise ValueError("The retained provider contract differs from the confirmation")
            releases[requirement.id] = release
        return releases

    def route(
        self,
        operation_id: UUID,
        providers: dict[str, Activation],
        approved: list[StepPlan],
        holdings: tuple[Resource, ...],
        *,
        inspect_only: bool = False,
    ) -> Callable[[ProviderMethod, CapabilityRequest], Awaitable[Values | StepPlan]]:
        async def dispatch(method: ProviderMethod, request: CapabilityRequest) -> Values | StepPlan:
            release = providers.get(request.capability)
            if inspect_only and method is not ProviderMethod.INSPECT:
                raise ValueError("Only provider readback is available during verification")
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
                    if method is ProviderMethod.PLAN
                    else WorkerMethod.PROVIDER_INSPECT,
                ),
            )
            if method is ProviderMethod.INSPECT and reply.facts is not None:
                return reply.facts
            step = reply.step
            version = next(
                c.version for c in release.manifest.provides if c.id == request.capability
            )
            if (
                method is not ProviderMethod.PLAN
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
