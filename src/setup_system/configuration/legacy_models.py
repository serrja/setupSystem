"""Frozen input models for the offline pre-plugin settings decoder."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from setup_system.domain.contracts import ContractModel

AdvertisedMode = Annotated[
    str, Field(pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\.[0-9]{1,5})?$")
]


class MonitorLayout(ContractModel):
    output: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
    mode: AdvertisedMode
    scale: StrictFloat = Field(default=1, ge=0.25, le=8, allow_inf_nan=False)
    x: StrictInt = Field(default=0, ge=-65535, le=65535)
    y: StrictInt = Field(default=0, ge=-65535, le=65535)


Section = Literal["base", "workspace"]
Monitor = MonitorLayout
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class BaseOptions(ContractModel):
    hyprland: StrictBool = False
    graphical_login: StrictBool = False
    plymouth: StrictBool = False
    timezone: str | None = Field(
        None, max_length=100, pattern=r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$"
    )
    locale: str | None = Field(None, max_length=64, pattern=r"^(C|[a-z]{2,3}_[A-Z]{2})\.UTF-8$")
    keyboard: str | None = Field(
        None, pattern=r"^[a-z][a-z0-9_-]{0,15}(,[a-z][a-z0-9_-]{0,15}){0,3}$"
    )

    @field_validator("timezone")
    @classmethod
    def canonical_zone(cls, value: str | None) -> str | None:
        if value and value.partition("/")[0] in {"posix", "right"}:
            raise ValueError("Choose a canonical timezone")
        return value


class Theme(ContractModel):
    compact_panel: StrictBool = False
    effects: StrictBool = False
    opacity: StrictFloat = Field(default=0.96, ge=0.75, le=1)
    rounding: StrictInt = Field(default=12, ge=0, le=24)


class CleanupMode(StrEnum):
    REMOVE = "remove"
    PURGE = "purge"
    AUTOREMOVE = "autoremove"


class WorkspaceOptions(ContractModel):
    desktop_theme: StrictBool = False
    theme: Theme = Theme()
    monitors: tuple[Monitor, ...] = Field(default=(), max_length=64)
    apps: tuple[Identifier, ...] = Field(default=(), max_length=128)
    languages: tuple[Identifier, ...] = Field(default=(), max_length=128)
    cleanup: tuple[Identifier, ...] = Field(default=(), max_length=128)
    cleanup_mode: CleanupMode = CleanupMode.REMOVE
    project: str | None = Field(None, max_length=4000, min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$")

    @field_validator("project")
    @classmethod
    def absolute_project(cls, value: str | None) -> str | None:
        if value is not None and (not Path(value).is_absolute() or ".." in Path(value).parts):
            raise ValueError("Choose an absolute project path")
        return value

    @model_validator(mode="after")
    def unique(self) -> Self:
        for values in (
            self.apps,
            self.languages,
            self.cleanup,
            tuple(m.output for m in self.monitors),
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate selection")
        return self
