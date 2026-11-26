"""Present the cleanup catalog and delegate reviewed package operations."""

from setup_system_sdk.backend import Backend
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.operations import Plan, Result
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.settings import PluginSettings

from setup_plugin_cleanup.models import catalog


class PluginActions(Backend):
    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        if context is None:
            return Result(outcome=Outcome.BLOCKED, message="Select a package cleanup provider")
        request = CapabilityRequest(
            capability="system.package-cleanup",
            step_key="packages",
            payload={"consumer": "cleanup", "action": "inspect", "inputs": {}},
        )
        return Result.model_validate((await context.inspect_provider(request))["result"])

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        return (
            [s for s in sorted(catalog()) if query.casefold() in s][:100]
            if provider == "candidates"
            else []
        )

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if context is None:
            raise ValueError("Select a package cleanup provider")
        return await self.delegated_plan(action_id, inputs, context)
