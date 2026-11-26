from __future__ import annotations

from typing import Literal

from pydantic import Field

from setup_system_sdk.plugin import PluginSettings


class Settings(PluginSettings):
    protection: Literal["strict", "standard"] = Field(
        description="Choose the enhanced tracking protection setting enforced by Firefox.",
        default="strict",
        title="Tracking protection",
    )
    https_only: bool = Field(
        description="Require secure connections when Firefox can upgrade a request to HTTPS.",
        default=True,
        title="HTTPS-only browsing",
    )
    install_ublock: bool = Field(
        description="Allow the Firefox enterprise policy to install uBlock Origin.",
        default=False,
        title="Install uBlock Origin from Mozilla Add-ons",
    )
