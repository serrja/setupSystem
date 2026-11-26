from __future__ import annotations

from typing import Literal

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    permission: Literal["inherit", "read-only", "workspace-write", ":read-only", ":workspace"] = (
        Field(
            description="Set Codex permissions or inherit the existing choice.",
            default="inherit",
            title="Sandbox or named permission profile",
        )
    )
    approval: Literal["inherit", "on-request"] = Field(
        description="Choose when the Codex client requests approval for commands.",
        default="inherit",
        title="Approval policy",
    )
    model: str | None = Field(
        description="Select a model reported by the installed Codex client.",
        default=None,
        max_length=160,
        title="Model",
        json_schema_extra={"ui": {"choice_provider": "models"}},
    )
    effort: str | None = Field(
        description="Select a reasoning effort supported by the active model.",
        default=None,
        max_length=64,
        title="Reasoning effort",
        json_schema_extra={"ui": {"choice_provider": "efforts"}},
    )
    instructions: bool = Field(
        description="Include the selected, digest-verified engineering instructions.",
        default=False,
        title="Include the pinned engineering instructions",
    )
