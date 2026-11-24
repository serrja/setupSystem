"""Filesystem and receipt ports used to inspect and restore recorded changes."""

from __future__ import annotations

from collections.abc import Collection
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from setup_system.domain.receipts import CreatedDirectory, FileChange, Operation, Ownership


@dataclass(frozen=True)
class RecoveryTarget:
    change: FileChange
    path: Path
    status: str
    original: bytes | None


class RecoveryJournal(Protocol):
    def writer(self) -> AbstractContextManager[None]: ...
    def load_operation(self, folder: Path, *, migrate: bool = False) -> Operation: ...
    def ownership(self, *, migrate: bool = False) -> Ownership: ...
    def backup_bytes(self, folder: Path, row: FileChange) -> bytes | None: ...
    def save_operation(self, folder: Path, operation: Operation) -> None: ...
    def save_ownership(self, index: Ownership) -> None: ...


class RecoveryFiles(Protocol):
    def restored_entry(self, row: FileChange) -> tuple[str, str]: ...
    def inspect(self, row: FileChange) -> tuple[Path, str]: ...
    def directories(
        self, op: Operation, paths: Collection[Path]
    ) -> list[tuple[Path, CreatedDirectory]]: ...
    def removable_directories(
        self, entries: list[tuple[Path, CreatedDirectory]], removed_files: set[Path]
    ) -> list[tuple[Path, CreatedDirectory]]: ...
    def conflict(self, target: RecoveryTarget) -> str: ...
    def restore_target(
        self, target: RecoveryTarget, operation: Operation, folder: Path
    ) -> tuple[str, bool]: ...
    def remove_directories(self, entries: list[tuple[Path, CreatedDirectory]]) -> None: ...
