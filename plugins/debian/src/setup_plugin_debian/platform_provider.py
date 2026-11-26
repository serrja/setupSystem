"""Read platform information through the selected Debian generation."""

from pathlib import Path
from typing import ClassVar, Literal

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.operations import Receipt, Result, StepPlan
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values

from setup_plugin_debian.adapters import inspection, platform


class PlatformRequest(Inputs):
    detail: Literal["target", "session", "observations"] = "target"


class PlatformProvider:
    request_model: ClassVar[type[Inputs]] = PlatformRequest

    async def inspect(self, request: CapabilityRequest, context: PluginContext) -> Values:
        query = PlatformRequest.model_validate(request.payload)
        arch, supported = await run_sync(platform.current_target)
        facts: Values = {"arch": arch, "supported": supported}
        if supported:
            facts["boot"] = await run_sync(platform.current_boot_identity)
            if query.detail == "session":
                state = await run_sync(inspection.inspect_system, Path.home())
                facts["session"] = state.model_dump(mode="json")
            elif query.detail == "observations":
                facts["observations"] = list(await run_sync(inspection.observations, Path.home()))
        return facts

    async def plan(self, request: CapabilityRequest, context: PluginContext) -> StepPlan:
        raise ValueError("Platform facts support inspection only")

    async def execute(self, step: StepPlan, context: PluginContext) -> Receipt:
        raise ValueError("Platform facts support inspection only")

    async def readback(
        self, step: StepPlan, receipt: Receipt | None, context: PluginContext
    ) -> Result:
        raise ValueError("Platform facts support inspection only")
