from __future__ import annotations

from pydantic import Field
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs


class Settings(PluginSettings):
    enterprise_host: str = Field(
        default="",
        max_length=253,
        pattern=r"^$|^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]([a-z0-9-]*[a-z0-9])?$",
        title="Approved enterprise host",
        description="Enter the exact GitHub Enterprise DNS hostname you intend to use.",
    )


class RetiredPackage(Inputs):
    name: str = Field(
        pattern=r"^[a-z0-9][a-z0-9+.-]+$",
        max_length=128,
        title="Formerly managed package",
        description="Choose one package removed from this plugin's install list.",
        json_schema_extra={"ui": {"choice_provider": "retired-packages"}},
    )


class CleanupKeys(Inputs):
    pass
