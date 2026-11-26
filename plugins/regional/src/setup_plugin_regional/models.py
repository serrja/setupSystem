"""Versioned regional and graphical boot observations and recovery evidence."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool
from setup_system_sdk.files.contracts import ContractModel
from setup_system_sdk.files.manifest_types import FileMode
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.settings import PluginSettings

RegionalKind = Literal["timezone", "locale", "keyboard"]


class RegionalReceipt(ContractModel):
    schema_version: Literal[1] = 1
    setting: RegionalKind
    before: str | None = None
    after: str
    status: Literal["running", "partial", "complete"] = "running"
    backup: Path | None = None
    mode: FileMode | None = None
    locale_gen_backup: Path | None = None
    locale_gen_existed: StrictBool | None = None


class Timezone(Inputs):
    value: str = Field(
        description="Use the selected native value for this operation.",
        title="Timezone",
        max_length=128,
        pattern=r"^[A-Za-z0-9/_.+-]+$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "timezone"}},
    )


class Locale(Inputs):
    value: str = Field(
        description="Use the selected native value for this operation.",
        title="UTF-8 locale",
        max_length=32,
        pattern=r"^(C|[a-z]{2,3}_[A-Z]{2})\.UTF-8$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "locale"}},
    )


class Keyboard(Inputs):
    layouts: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,15}$")]] = Field(
        title="Keyboard layouts in order",
        description="The first selected layout is the default.",
        min_length=1,
        max_length=4,
        json_schema_extra={"ui": {"widget": "selection_list", "choice_provider": "keyboard"}},
    )


Settings = PluginSettings
