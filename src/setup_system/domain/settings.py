"""Versioned desired state; observed machine facts belong to inspection results."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from setup_system.domain.catalog import ComponentID
from setup_system.domain.contracts import ContractModel
from setup_system.domain.displays import MonitorLayout


class Section(StrEnum):
    BASE = "base"
    WORKSPACE = "workspace"


class ThemeSettings(ContractModel):
    name: Literal["catppuccin_mocha_green"] = "catppuccin_mocha_green"
    compact_panel: StrictBool = False
    opacity: StrictFloat = Field(default=0.96, ge=0.75, le=1, allow_inf_nan=False)
    rounding: StrictInt = Field(default=12, ge=0, le=24)
    effects: StrictBool = False


class SystemSettings(ContractModel):
    schema_version: Literal[1] = 1

    hyprland: StrictBool = False
    graphical_login: StrictBool = False
    timezone: str | None = Field(
        default=None, max_length=100, pattern=r"^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*$"
    )
    locale: str | None = Field(default=None, pattern=r"^(?:C|[a-z]{2}_[A-Z]{2})\.UTF-8$")
    keyboard: Literal["us", "us,ru"] | None = None
    plymouth: StrictBool = False

    @field_validator("timezone")
    @classmethod
    def canonical_zone(cls, value: str | None) -> str | None:
        if value and value.partition("/")[0] in {"posix", "right"}:
            raise ValueError("Choose a canonical IANA timezone")
        return value


# Published v0.4.0 keys are an immutable migration boundary.
V1_APP_KEYS = frozenset({"neovim", "firefox", "codex", "chatgpt", "happ", "developer_tools"})
V1_USER_KEYS = V1_APP_KEYS | {
    "schema_version",
    "theme",
    "language_profiles",
    "optional_apps",
    "cleanup",
    "cleanup_mode",
}


class UserSettings(ContractModel):
    schema_version: Literal[2] = 2
    theme: ThemeSettings = Field(default_factory=ThemeSettings)
    desktop_theme: StrictBool = False
    monitors: tuple[MonitorLayout, ...] = ()
    apps: tuple[ComponentID, ...] = ()
    language_profiles: tuple[ComponentID, ...] = ()
    optional_apps: tuple[ComponentID, ...] = ()
    cleanup: tuple[str, ...] = ()
    cleanup_mode: Literal["remove", "purge", "autoremove"] = "remove"

    @model_validator(mode="before")
    @classmethod
    def migrate(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        version = data.get("schema_version")
        if version is not None and type(version) is not int:
            raise ValueError("Invalid settings schema version")
        if version == 1 or (version is None and V1_APP_KEYS.intersection(data)):
            values = dict(data)
            if values.keys() - V1_USER_KEYS or any(
                type(values[name]) is not bool for name in V1_APP_KEYS.intersection(values)
            ):
                raise ValueError("Conflicting or invalid version 1 app selections")
            values["apps"] = [name for name in sorted(V1_APP_KEYS) if values.pop(name, False)]
            values["schema_version"] = 2
            return values
        return data

    @field_validator("apps", "optional_apps", "language_profiles", "cleanup")
    @classmethod
    def unique_selection(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Repeated catalog selection")
        return value
