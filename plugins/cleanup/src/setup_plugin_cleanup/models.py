"""Typed inputs and settings for this plugin."""

import tomllib
from importlib.resources import files

from pydantic import Field, field_validator
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.settings import PluginSettings


class Candidates(Inputs):
    packages: list[str] = Field(
        title="Removal candidates",
        min_length=1,
        max_length=64,
        description="Select each package included in the confirmed APT removal closure.",
        json_schema_extra={
            "ui": {
                "widget": "selection_list",
                "choice_provider": "candidates",
                "allow_custom": False,
            }
        },
    )

    @field_validator("packages")
    @classmethod
    def reviewed(cls, names: list[str]) -> list[str]:
        if len(set(names)) != len(names) or not set(names) <= catalog().keys():
            raise ValueError("Select distinct candidates from the plugin catalog.")
        return names


class PurgeCandidates(Candidates):
    """Require absence of package configuration during purge readback."""


Settings = PluginSettings


def catalog() -> dict[str, object]:
    raw = files("setup_plugin_cleanup").joinpath("resources", "candidates.toml").read_text()
    return dict(tomllib.loads(raw)["candidates"])
