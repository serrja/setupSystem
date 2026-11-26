from __future__ import annotations

from typing import Literal

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


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
