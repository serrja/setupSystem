"""Versioned regional and graphical boot observations and recovery evidence."""

from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.files.contracts import ContractModel
from setup_system_sdk.files.manifest_types import Digest, FileMode, FrozenMap
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan
from setup_system_sdk.settings import PluginSettings

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


class Confirmation(Inputs):
    action: Literal[LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.CONFIGURE]
    plan: Plan
    operation: UUID
    step_key: Literal["backup", "config"]


Settings = PluginSettings
