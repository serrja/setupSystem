"""Shared installation lifecycle vocabulary and bounded journal serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast


class InstallStage(StrEnum):
    METADATA = "metadata"
    RESOLUTION = "resolution"
    DOWNLOADS = "downloads"
    USER_PREFETCH = "user-prefetch"
    PACKAGES = "packages"
    SOURCE = "source"
    HAPP_SERVICE = "happ-service"
    USER_RUNTIME = "user-runtime"
    USER = "user"
    USER_DOWNLOADS = "user-downloads"
    TEMPORARY_NETWORK = "temporary-network"
    PACKAGE_DOWNLOADS = "package-downloads"
    TEMPORARY_NETWORK_CLEANUP = "temporary-network-cleanup"


class InstallStatus(StrEnum):
    RUNNING = "running"
    PREPARED = "prepared"
    PACKAGES_COMPLETE = "packages-complete"
    SYSTEM_COMPLETE = "system-complete"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class InstallJournal:
    status: InstallStatus = InstallStatus.RUNNING
    stage: InstallStage = InstallStage.METADATA
    completed: tuple[str, ...] = ()
    error: str | None = None
    observations: dict[str, object] = field(default_factory=dict)

    @classmethod
    def parse(cls, text: str) -> InstallJournal:
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("Installation journal must be an object")
        if type(document.get("schema")) is not int or document["schema"] != 1:
            raise ValueError("Installation journal has an unsupported schema")
        raw_status, raw_stage = document.get("status"), document.get("stage")
        if not isinstance(raw_status, str) or not isinstance(raw_stage, str):
            raise ValueError("Installation journal has an unknown current stage or status")
        try:
            status = InstallStatus(raw_status)
            stage = InstallStage(raw_stage)
        except (TypeError, ValueError) as exc:
            raise ValueError("Installation journal has an unknown current stage or status") from exc
        completed = document.get("completed", [])
        if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
            raise ValueError("Installation journal has invalid completed stages")
        error = document.get("error")
        if error is not None and not isinstance(error, str):
            raise ValueError("Installation journal has an invalid error record")
        observations = {
            key: value
            for key, value in document.items()
            if key not in {"schema", "status", "stage", "completed", "error"}
        }
        return cls(status, stage, tuple(completed), error, cast(dict[str, object], observations))

    def checkpoint(
        self,
        stage: InstallStage,
        completed: str | None = None,
        *,
        status: InstallStatus | None = None,
    ) -> None:
        self.stage = stage
        if status is not None:
            self.status = status
        if completed is not None:
            self.completed = tuple(dict.fromkeys((*self.completed, completed)))

    def failed(self, error: BaseException) -> None:
        self.status = InstallStatus.FAILED
        self.error = type(error).__name__

    def merge_user(self, status: str, stage: str | None, completed: tuple[str, ...]) -> None:
        if status not in {"running", "complete", "failed"}:
            raise ValueError("Invalid user installation stage or status")
        try:
            selected = self.stage if stage is None else InstallStage(stage)
        except ValueError as exc:
            raise ValueError("Invalid user installation stage or status") from exc
        if any(item not in {"user-downloads", "user-configuration"} for item in completed):
            raise ValueError("Invalid completed user installation stage")
        self.stage = selected
        self.status = InstallStatus(status)
        self.completed = tuple(dict.fromkeys((*self.completed, *completed)))

    def serialize(self) -> bytes:
        document: dict[str, object] = {
            **self.observations,
            "schema": 1,
            "status": self.status.value,
            "stage": self.stage.value,
            "completed": list(self.completed),
        }
        if self.error is not None:
            document["error"] = self.error
        return (json.dumps(document, indent=2) + "\n").encode()
