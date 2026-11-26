from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs


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
        json_schema_extra={"ui": {"choice_provider": "efforts", "depends_on": ["model"]}},
    )
    instructions: bool = Field(
        description="Include the selected, digest-verified engineering instructions.",
        default=False,
        title="Include the pinned engineering instructions",
    )


class PolicySource(Inputs):
    repository: Literal["serrja/baseRules"]
    document: Literal["docs/engineering.md"]
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SignIn(Inputs):
    """Verify the package-installed client before starting the interactive sign-in."""


class Project(Inputs):
    project: Path | None = Field(
        default=None,
        title="Project checkout",
        description="Leave empty for the current user's config.",
    )


class McpServer(Project):
    name: str = Field(
        description="Name the MCP server entry in the selected Codex configuration.",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
        title="MCP server name",
    )
    url: AnyHttpUrl = Field(
        description="Enter the approved HTTPS endpoint for this MCP server.",
        title="Reviewed HTTPS MCP endpoint",
        max_length=2048,
    )
    enable: bool = Field(
        description="Enable this server in the selected Codex configuration.",
        default=False,
        title="Enable this server for the selected scope",
    )

    @field_validator("url")
    @classmethod
    def https_endpoint(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError(
                "Choose a public HTTPS endpoint without credentials, queries or fragments"
            )
        return value
