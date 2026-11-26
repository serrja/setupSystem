from __future__ import annotations

from pydantic import BaseModel, Field
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs


class IndexWheel(BaseModel):
    url: str
    hashes: dict[str, str]


class Settings(PluginSettings):
    type_checker: bool = Field(
        default=True,
        title="Install basedpyright",
        description="Add the Python language server and checker alongside Ruff.",
    )


class Package(Inputs):
    name: str
    version: str
    marker: str | None = None
    wheels: list[IndexWheel]
