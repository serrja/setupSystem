"""Versioned desired state; observed machine facts belong to inspection results."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Section(StrEnum):
    BASE = "base"
    WORKSPACE = "workspace"


class ThemeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: Literal["catppuccin_mocha_green"] = "catppuccin_mocha_green"
    compact_panel: bool = False
    opacity: float = Field(default=0.96, ge=0.75, le=1, allow_inf_nan=False)
    rounding: int = Field(default=12, ge=0, le=24)
    effects: bool = False


class SystemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    hyprland: bool = False
    graphical_login: bool = False
    timezone: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_+./-]+$")
    keyboard: Literal["us", "us,ru"] = "us,ru"
    plymouth: bool = False


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    theme: ThemeSettings = Field(default_factory=ThemeSettings)
    neovim: bool = False
    firefox: bool = False
    codex: bool = False
    chatgpt: bool = False
    happ: bool = False
    developer_tools: bool = False
    language_profiles: tuple[Literal["python", "typescript_vue", "dart_flutter", "cpp"], ...] = ()
    optional_apps: tuple[Literal["obsidian", "telegram", "docker", "libreoffice"], ...] = ()
    cleanup: tuple[str, ...] = ()
    cleanup_mode: Literal["remove", "purge", "autoremove"] = "remove"
