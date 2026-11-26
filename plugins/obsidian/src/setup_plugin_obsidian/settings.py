from __future__ import annotations

from pathlib import Path

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    vault: Path | None = Field(
        description="Select an existing vault to use for vault-specific actions.",
        default=None,
        title="Selected vault",
        json_schema_extra={"ui": {"widget": "path"}},
    )
