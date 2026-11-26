from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import ClassVar

from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import ActionMode, CancellationPolicy, Outcome
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.resources import ResourceEffect
from setup_system_sdk.operations import Receipt, StepPlan
from setup_system_sdk.providers import CapabilityRequest, Provider
from setup_system_sdk.schemas.common import ActionId, Inputs, Values
from setup_system_sdk.schemas.operation import Plan, Resource, Result
from setup_system_sdk.settings import PluginSettings

_ownership: ContextVar[tuple[Resource, ...]] = ContextVar("owned_resources", default=())


def owned_resources() -> tuple[Resource, ...]:
    return _ownership.get()


@contextmanager
def ownership_context(resources: tuple[Resource, ...]) -> Iterator[None]:
    token = _ownership.set(resources)
    try:
        yield
    finally:
        _ownership.reset(token)


@dataclass(frozen=True)
class Command:
    """The action key and input contract registered by the active backend."""

    id: StrEnum
    schema: type[Inputs]
    plan: Callable[[PluginSettings, Inputs, PluginContext | None], Awaitable[Plan]]
    observe: Callable[[Plan, PluginSettings, Inputs, PluginContext | None], Awaitable[Result]]

    def validate(self, values: Values) -> Inputs:
        return self.schema.model_validate(values)

    async def readback(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        """Bind successful plugin readback to the exact set of confirmed resources."""
        result = await self.observe(plan, settings, inputs, context)
        complete = result.outcome in {
            Outcome.COMPLETE,
            Outcome.CHANGED,
            Outcome.NO_CHANGES,
            Outcome.NEEDS_REBOOT,
        }
        expected = {(r.kind, r.identity): r for r in plan.resources}
        observed = {(r.kind, r.identity): r for r in result.resources}
        if (
            len(expected) != len(plan.resources)
            or len(observed) != len(result.resources)
            or observed.keys() - expected.keys()
            or (complete and observed.keys() != expected.keys())
        ):
            return Result(
                outcome=Outcome.PARTIAL, message="Readback omitted or repeated a resource."
            )
        for key, row in observed.items():
            approved = expected[key]
            if not row.verified and complete:
                return Result(
                    outcome=Outcome.PARTIAL, message="The handler did not verify every resource."
                )
            if not row.verified:
                continue
            if (
                (
                    row.ownership,
                    row.effect,
                    row.cleanup,
                    row.consumers,
                    row.source_sha256,
                    row.before,
                    row.before_permissions,
                )
                != (
                    approved.ownership,
                    approved.effect,
                    approved.cleanup,
                    approved.consumers,
                    approved.source_sha256,
                    approved.before,
                    approved.before_permissions,
                )
                or (approved.after is not None and row.after != approved.after)
                or (approved.effect is ResourceEffect.REMOVE and row.after is not None)
                or (approved.remote_id is not None and row.remote_id != approved.remote_id)
                or (
                    approved.permissions is not None
                    and row.permissions != approved.permissions
                    and complete
                )
            ):
                return Result(
                    outcome=Outcome.PARTIAL, message="Readback differs from the confirmation."
                )
        return result


class Backend:
    """Commands advertised by a plugin require matching implementations."""

    providers: ClassVar[dict[str, Provider]] = {}
    contract_versions: ClassVar[dict[str, str]] = {}
    settings_model: ClassVar[type[PluginSettings]] = PluginSettings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {}

    delegated_actions: ClassVar[dict[str, str]] = {}
    delegated_step_key: ClassVar[str] = "provider"

    async def bind_plan(
        self, command: Command, settings: PluginSettings, inputs: Inputs, context: PluginContext
    ) -> Plan:
        """Bind locally prepared resources to the registered handler before confirmation."""
        plan = await command.plan(settings, inputs, context)
        if plan.steps or not context.action:
            return plan
        if context.action.mode in {ActionMode.READ_ONLY, ActionMode.MANUAL} or plan.outcome not in {
            Outcome.READY,
            Outcome.NO_CHANGES,
        }:
            return plan.model_copy(update={"resources": []})
        if not plan.resources and plan.outcome is Outcome.NO_CHANGES:
            return plan
        step = StepPlan(
            step_key="config",
            executor=context.executor,
            handler=command.id.value,
            contract_version="1",
            permissions=frozenset(context.action.permissions),
            cancellation=CancellationPolicy.SAFE_BOUNDARY
            if {Permission.ROOT, Permission.SU, Permission.SUDO}.intersection(
                context.action.permissions
            )
            else CancellationPolicy.TERMINATE,
            payload={
                "settings": settings.model_dump(mode="json"),
                "inputs": inputs.model_dump(mode="json"),
            },
            preconditions={"proposal": plan.model_dump(mode="json")},
            resources=tuple(plan.resources),
        )
        return plan.model_copy(update={"steps": (step,)})

    async def delegated_plan(self, action: str, inputs: Inputs, context: PluginContext) -> Plan:
        cap = self.delegated_actions[action]
        request = CapabilityRequest(
            capability=cap,
            step_key=self.delegated_step_key,
            payload={
                "consumer": context.executor.plugin_id,
                "action": action,
                "inputs": inputs.model_dump(mode="json"),
            },
        )
        facts = await context.inspect_provider(request)
        proposal = Plan.model_validate(facts["proposal"])
        if proposal.outcome not in {Outcome.READY, Outcome.NO_CHANGES}:
            return proposal.model_copy(update={"resources": []})
        if not proposal.resources and proposal.outcome is Outcome.NO_CHANGES:
            return proposal
        step = await context.plan_provider(request)
        if step.resources != tuple(proposal.resources) or step.preconditions.get(
            "proposal"
        ) != proposal.model_dump(mode="json"):
            raise ValueError("Provider facts changed during preview")
        return proposal.model_copy(update={"steps": (step,)})

    async def confirm_result(
        self,
        command: Command,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext,
    ) -> Result:
        if command.id.value in self.delegated_actions:
            if not plan.steps and plan.outcome is Outcome.NO_CHANGES:
                return Result(outcome=Outcome.NO_CHANGES, message=plan.summary)
            (step,) = plan.steps
            if step.capability != self.delegated_actions[command.id.value]:
                raise ValueError("Unexpected delegated step")
            request = CapabilityRequest(
                capability=step.capability,
                step_key=step.step_key,
                payload={
                    **step.payload,
                    "evidence": [r.model_dump(mode="json") for r in step.resources],
                },
            )
            return Result.model_validate((await context.inspect_provider(request))["result"])
        return await command.readback(plan, settings, inputs, context)

    def commands(self) -> dict[str, Command]:
        """Register concrete handlers with the action model accepted by each command."""
        return {
            key.value: Command(key, schema, partial(self.plan, key), self.verify)
            for key, schema in self.action_inputs.items()
        }

    def command(self, action_id: ActionId) -> Command:
        return self.commands()[action_id]

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        raise NotImplementedError

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        raise NotImplementedError

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        raise NotImplementedError

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        return []

    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt:
        return await context.execute_native()

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        settings = self.settings_model.model_validate(step.payload["settings"])
        inputs = self.command(step.handler).schema.model_validate(step.payload["inputs"])
        proposal = Plan.model_validate(step.preconditions["proposal"])
        return await self.command(step.handler).readback(proposal, settings, inputs, context)
