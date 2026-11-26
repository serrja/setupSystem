"""Filesystem and receipt ports used to inspect and restore recorded changes."""

from __future__ import annotations

from collections.abc import Collection
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from setup_system_sdk.enums.resources import RecoveryStep
from setup_system_sdk.files.receipts import CreatedDirectory, FileChange, Operation, Ownership
from setup_system_sdk.schemas.common import Inputs


class RecoveryEntry(Inputs):
    path: Path
    status: RecoveryStep
    retained_path: Path | None = None

    def description(self) -> str:
        text = f"{self.status.value} · {self.path}"
        if self.retained_path:
            text += f" · Displaced file retained at {self.retained_path}"
        return text


@dataclass(frozen=True)
class RecoveryTarget:
    change: FileChange
    entry: RecoveryEntry
    original: bytes | None


class RecoveryJournal(Protocol):
    def writer(self) -> AbstractContextManager[None]: ...
    def load_operation(self, folder: Path) -> Operation: ...
    def ownership(self) -> Ownership: ...
    def backup_bytes(self, folder: Path, row: FileChange) -> bytes | None: ...
    def save_operation(self, folder: Path, operation: Operation) -> None: ...
    def save_ownership(self, index: Ownership) -> None: ...


class RecoveryFiles(Protocol):
    def restored_entry(self, row: FileChange) -> RecoveryEntry: ...
    def inspect(self, row: FileChange) -> RecoveryEntry: ...
    def directories(
        self, op: Operation, paths: Collection[Path]
    ) -> list[tuple[Path, CreatedDirectory]]: ...
    def removable_directories(
        self, entries: list[tuple[Path, CreatedDirectory]], removed_files: set[Path]
    ) -> list[tuple[Path, CreatedDirectory]]: ...
    def conflict(self, target: RecoveryTarget) -> str: ...
    def restore_target(
        self, target: RecoveryTarget, operation: Operation, folder: Path
    ) -> tuple[RecoveryStep, bool]: ...
    def remove_directories(self, entries: list[tuple[Path, CreatedDirectory]]) -> None: ...
