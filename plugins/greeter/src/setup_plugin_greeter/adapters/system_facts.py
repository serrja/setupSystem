"""Observed clock and storage facts reported to the setup application."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import Field, StrictBool, StrictInt
from setup_system_sdk.files.contracts import ContractModel

FileMode = Annotated[StrictInt, Field(ge=0, le=0o7777)]


class EncryptionState(StrEnum):
    ENCRYPTED = "encrypted"
    LOCKED = "locked"
    UNKNOWN = "unknown"


class EncryptionInfo(ContractModel):
    state: EncryptionState
    layers: tuple[str, ...]
    detail: str


class EncryptionSnapshot(ContractModel):
    root: EncryptionInfo
    home: EncryptionInfo


class ClockSnapshot(ContractModel):
    """Current clock properties and installed (not necessarily active) time providers."""

    timezone: str | None
    ntp_enabled: StrictBool | None = None
    synchronized: StrictBool | None = None
    rtc_local: StrictBool | None = None
    can_ntp: StrictBool | None = None
    providers: tuple[str, ...] = ()
    providers_known: StrictBool = False


class SystemSnapshot(ContractModel):
    encryption: EncryptionSnapshot
    clock: ClockSnapshot


class InspectionPaths(ContractModel):
    zoneinfo: Path = Path("/usr/share/zoneinfo")
    timezone: Path = Path("/etc/timezone")
    localtime: Path = Path("/etc/localtime")
