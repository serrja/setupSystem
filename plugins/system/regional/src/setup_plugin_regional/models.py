"""Versioned regional and graphical boot observations and recovery evidence."""

from pathlib import Path
from typing import Literal

from pydantic import StrictBool

from setup_system.domain.contracts import ContractModel
from setup_system.domain.system_facts import FileMode

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
