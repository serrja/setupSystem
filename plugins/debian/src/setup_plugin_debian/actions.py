"""Debian platform availability and package management capabilities."""

import anyio
from setup_system_sdk.backend import Backend
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.operations import Result
from setup_system_sdk.settings import PluginSettings

from setup_plugin_debian.adapters.platform import current_target


class PluginActions(Backend):
    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        arch, supported = await anyio.to_thread.run_sync(current_target)
        status = "available" if supported else "unsupported platform"
        return Result(
            outcome=Outcome.NO_CHANGES if supported else Outcome.BLOCKED,
            message=f"Debian package integration ({arch}): {status}",
        )
