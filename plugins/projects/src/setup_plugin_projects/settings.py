from __future__ import annotations

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    offline: bool = Field(
        description="Use template releases that have already been downloaded.",
        default=False,
        title="Use cached template releases",
    )
