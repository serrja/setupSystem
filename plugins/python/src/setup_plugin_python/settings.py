from __future__ import annotations

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    type_checker: bool = Field(
        default=True,
        title="Install basedpyright",
        description="Add the Python language server and checker alongside Ruff.",
    )
