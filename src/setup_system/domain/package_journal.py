"""Typed checkpoints for one privileged package acquisition and installation."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from setup_system.domain.contracts import ContractModel


class InstallStage(StrEnum):
    METADATA = "metadata"
    RESOLUTION = "resolution"
    DOWNLOADS = "downloads"
    PACKAGES = "packages"
    SOURCE = "source"
    HAPP_SERVICE = "happ-service"


class InstallStatus(StrEnum):
    RUNNING = "running"
    PREPARED = "prepared"
    PACKAGES_COMPLETE = "packages-complete"
    SYSTEM_COMPLETE = "system-complete"
    FAILED = "failed"


class InstallJournal(ContractModel):
    schema_version: Literal[2] = Field(default=2, alias="schema")
    status: InstallStatus = InstallStatus.RUNNING
    stage: InstallStage = InstallStage.METADATA
    completed: tuple[InstallStage, ...] = ()
    error: str | None = None

    def checkpoint(
        self,
        stage: InstallStage,
        completed: str | None = None,
        *,
        status: InstallStatus | None = None,
    ) -> InstallJournal:
        finished = self.completed
        if completed is not None:
            finished = tuple(dict.fromkeys((*finished, InstallStage(completed))))
        return self.validated(stage=stage, status=status or self.status, completed=finished)

    def failed(self, error: BaseException) -> InstallJournal:
        return self.validated(status=InstallStatus.FAILED, error=type(error).__name__)

    def serialize(self) -> bytes:
        return (self.model_dump_json(indent=2, by_alias=True, exclude_none=True) + "\n").encode()
