from __future__ import annotations

from typing import Literal

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    mode: Literal["rootless", "rootful"] = Field(
        default="rootless",
        title="Daemon mode",
        description="Rootful socket access grants root-equivalent control. Review the mode action "
        "to select an Engine. Saving this preference does not start a daemon or grant access.",
    )
