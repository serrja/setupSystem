from __future__ import annotations

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    compact: bool = Field(
        description="Reduce the panel spacing and padding.", default=True, title="Compact top panel"
    )
    opacity: float = Field(
        description="Set the opacity of the panel background.",
        default=0.96,
        ge=0.75,
        le=1.0,
        allow_inf_nan=False,
        title="Panel opacity",
    )
    rounding: int = Field(
        description="Set the corner radius in pixels.",
        default=12,
        ge=0,
        le=24,
        title="Panel corner radius",
    )
