from __future__ import annotations

from typing import Annotated

from pydantic import Field
from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    effects: bool = Field(
        description="Enable the compositor effects when writing the Hyprland configuration.",
        default=False,
        title="Compositor effects",
    )
    rounding: int = Field(
        description="Set the corner radius in pixels.",
        default=12,
        ge=0,
        le=24,
        title="Window corner radius",
    )
    keyboard: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,15}$")]] = Field(
        default=["us"],
        min_length=1,
        max_length=4,
        title="Session keyboard layouts",
        description="Select up to four XKB layouts. The first is the session default.",
        json_schema_extra={"ui": {"widget": "selection_list", "choice_provider": "keyboard"}},
    )
