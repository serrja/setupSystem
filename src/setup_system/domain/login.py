"""The checked inputs to an explicitly selected graphical login change."""

from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field, StrictBool

from setup_system.domain.contracts import ContractModel
from setup_system.domain.identity import TargetUser
from setup_system.domain.manifest_types import Digest, FrozenMap
from setup_system.domain.system_facts import FileMode, SystemSnapshot


class LoginConsent(ContractModel):
    previous_target: str = Field(pattern=r"^[A-Za-z0-9_.@:-]{1,220}\.target$")
    intended: Digest
    manager_enabled: StrictBool | None
    observed: FrozenMap[str, Digest | None]
    deferred: tuple[str, ...]

    def verify(self, current: LoginConsent) -> None:
        if (
            current.deferred
            or self.previous_target != current.previous_target
            or self.intended != current.intended
            or (
                self.manager_enabled is not None and self.manager_enabled != current.manager_enabled
            )
            or any(
                key not in current.observed or current.observed[key] != value
                for key, value in self.observed.items()
            )
        ):
            raise ValueError("Desktop or GRUB inputs changed after consent. Review a fresh plan")


class DesktopFile(ContractModel):
    path: Path
    content: bytes
    mode: FileMode
    observed: str | None
    observed_mode: FileMode | None


class DesktopPlan(ContractModel):
    model_config = ConfigDict(strict=True)
    target: TargetUser
    environment: FrozenMap[str, str]
    files: tuple[DesktopFile, ...]
    snapshot: SystemSnapshot
    consent: LoginConsent
    grub_observed: Digest | None
    receipt_observed: str | None
    grub_managed: StrictBool = False
    grub_inputs: str = ""

    @property
    def previous_target(self) -> str:
        return self.consent.previous_target
