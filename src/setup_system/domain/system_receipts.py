"""Versioned regional and graphical boot observations and recovery evidence."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest, FrozenMap
from setup_system.domain.system_commands import RegionalKind
from setup_system.domain.system_facts import FileMode

BootStatus = Literal[
    "prepared",
    "installing_prerequisites",
    "applying",
    "needs_reboot",
    "restoring",
    "partial",
    "restored",
]


class BootFile(ContractModel):
    backup: Path | None
    mode: FileMode | None
    sha256: Digest | None
    after: Digest | None = None
    after_mode: FileMode | None = None
    after_device: StrictInt | None = Field(default=None, ge=0)
    after_inode: StrictInt | None = Field(default=None, gt=0)
    apply_area: Path | None = None
    restore_area: Path | None = None
    verified: StrictBool = False

    @model_validator(mode="after")
    def check_backup(self) -> Self:
        if (self.backup is None) != (self.mode is None) or (self.backup is None) != (
            self.sha256 is None
        ):
            raise ValueError("Inconsistent original boot file state")
        if self.verified and self.after is None:
            raise ValueError("Verified boot write needs an expected digest")
        return self


class BootReceipt(ContractModel):
    schema_version: Literal[1] = 1
    kernel: str = Field(pattern="^[0-9][a-zA-Z0-9.+_-]{0,100}$")
    status: BootStatus
    files: FrozenMap[Path, BootFile]


class UnlockPlan(ContractModel):
    description: str
    identity: Digest | None
    blockers: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()


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
