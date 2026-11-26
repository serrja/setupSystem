from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs


class Settings(PluginSettings):
    vault: Path | None = Field(
        description="Select an existing vault to use for vault-specific actions.",
        default=None,
        title="Selected vault",
        json_schema_extra={"ui": {"widget": "path"}},
    )


class NewVault(Inputs):
    path: Path = Field(
        description="Choose the directory for the new vault inside your home.",
        title="New vault directory",
        json_schema_extra={"ui": {"widget": "path"}},
    )


class OpenVault(Inputs):
    """Ask the official native application to open the selected vault."""


class Hotkey(Inputs):
    command_id: str = Field(
        description="Choose a command reported by the currently open vault.",
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$",
        title="Command",
        json_schema_extra={"ui": {"choice_provider": "commands"}},
    )


class Theme(Inputs):
    name: str = Field(
        description="Choose an installed item from the selected vault.",
        min_length=1,
        max_length=100,
        pattern=r"^[\w .()-]+$",
        title="Installed theme",
        json_schema_extra={"ui": {"choice_provider": "themes"}},
    )


class EditorSnippet(Inputs):
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[\w][\w .()-]*$",
        title="Installed CSS snippet",
        description="Choose a trusted snippet already present in the selected vault.",
        json_schema_extra={"ui": {"choice_provider": "snippets"}},
    )
    enabled: bool = Field(
        description="Set the selected feature or snippet to enabled or disabled.",
        default=True,
        title="Enable snippet",
    )


class CoreFeature(Inputs):
    feature: Literal["bookmarks", "outline", "daily-notes", "templates"] = Field(
        default="bookmarks",
        description="Choose the built-in vault feature whose setting will change.",
    )
    enabled: bool = Field(
        default=True, description="Set the selected feature or snippet to enabled or disabled."
    )
