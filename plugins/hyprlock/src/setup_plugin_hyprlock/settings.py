from __future__ import annotations

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    fade_on_empty: bool = Field(
        description="Fade the lock-screen input while no password characters have been entered.",
        default=False,
        title="Fade empty password field",
    )
