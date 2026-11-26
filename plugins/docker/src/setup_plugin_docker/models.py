from __future__ import annotations

from typing import Literal

from pydantic import Field
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs


class Settings(PluginSettings):
    mode: Literal["rootless", "rootful"] = Field(
        default="rootless",
        title="Daemon mode",
        description="Rootful socket access grants root-equivalent control. Review the mode action "
        "to select an Engine. Saving this preference does not start a daemon or grant access.",
    )


class Context(Inputs):
    name: str = Field(
        description="Identify the Docker context whose configuration this action uses.",
        min_length=1,
        max_length=64,
        pattern="^[a-z][a-z0-9_-]*$",
        title="New context name",
    )


class Image(Inputs):
    digest: str = Field(
        description="Select an unused local image by its content digest.",
        pattern=r"^sha256:[a-f0-9]{64}$",
        title="Unused image digest",
        json_schema_extra={"ui": {"choice_provider": "unused_images"}},
    )
