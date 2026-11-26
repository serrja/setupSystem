from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs


class Settings(PluginSettings):
    languages: list[Literal["python"]] = Field(
        description="Select the language support to configure in Neovim.",
        default_factory=list,
        max_length=1,
        title="Editor languages",
        json_schema_extra={"ui": {"widget": "selection_list"}},
    )
    clipboard: bool = Field(
        description="Use the desktop clipboard when a Wayland session is available.",
        default=True,
        title="Use Wayland clipboard when available",
    )
    primary_editor: bool = Field(
        description="Set Neovim as the editor for subsequent login sessions.",
        default=True,
        title="Set Neovim as the login editor",
    )


class Search(Inputs):
    exclude_globs: list[str] = Field(min_length=1, max_length=128)

    @field_validator("exclude_globs")
    @classmethod
    def verify_globs(cls, values: list[str]) -> list[str]:
        if any(not v or not v.isprintable() or v.startswith("!") or len(v) > 512 for v in values):
            raise ValueError("Invalid editor search exclusion glob")
        return values
