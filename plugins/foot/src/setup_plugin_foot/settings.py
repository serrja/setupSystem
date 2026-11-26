from __future__ import annotations

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    font_size: int = Field(
        description="Set the terminal font size in points when writing the Foot configuration.",
        default=11,
        ge=6,
        le=48,
        title="Terminal font size",
        json_schema_extra={"ui": {"units": "pt"}},
    )
