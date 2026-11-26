from __future__ import annotations

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    enterprise_host: str = Field(
        default="",
        max_length=253,
        pattern=r"^$|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]([a-z0-9-]*[a-z0-9])?$",
        title="Approved enterprise host",
        description="Enter the exact GitHub Enterprise DNS hostname you intend to use.",
    )
