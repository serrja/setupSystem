"""Typed inputs and settings for this plugin."""

from typing import Literal

from pydantic import Field
from setup_system_sdk.schemas.common import EnvironmentValue, Inputs
from setup_system_sdk.settings import PluginSettings


class Terminal(EnvironmentValue):
    name: Literal["TERM"] = Field(
        default="TERM",
        description="Set the approved terminal environment variable for this invocation.",
    )
    value: Literal["xterm-256color", "xterm", "dumb"] = Field(
        default="xterm-256color",
        description="Choose the terminal value presented to the shell during this check.",
    )


class Startup(Inputs):
    flags: tuple[Literal["-c", "-ic", "-lic"], ...] = Field(
        default=("-c", "-ic", "-lic"),
        min_length=1,
        max_length=3,
        description="Native startup modes to verify in an isolated home.",
    )
    environment: tuple[Terminal, ...] = Field(
        default=(), max_length=1, description="Terminal settings for this check."
    )


Settings = PluginSettings
