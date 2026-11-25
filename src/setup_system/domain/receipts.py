"""Versioned user-file receipt contracts and recovery decisions."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
RunId = Annotated[str, Field(pattern="^" + RUN_ID.pattern + "$")]


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ReceiptRecord(ContractModel):
    model_config = ConfigDict(strict=True, frozen=False)


class FileState(ReceiptRecord):
    sha256: Digest
    mode: int = Field(ge=0, le=0o777)
    uid: int = Field(ge=0)
    gid: int = Field(ge=0)


class CreatedDirectory(ReceiptRecord):
    relative: str
    device: int
    inode: int


class FileChange(ReceiptRecord):
    relative: str
    before: FileState | None
    backup: str | None
    previously_owned: bool
    after: FileState
    verified: bool = False
    restore_directory: CreatedDirectory | None = None
    retained_after: CreatedDirectory | None = None
    apply_directory: CreatedDirectory | None = None
    prepared_inode: int | None = None

    @model_validator(mode="after")
    def validate_backup(self) -> Self:
        expected = "files/" + self.relative if self.before is not None else None
        if self.backup != expected:
            raise ValueError("Backup does not match recorded original file")
        return self


class Operation(ReceiptRecord):
    schema_version: Literal[1] = 1
    run_id: RunId
    status: Literal["running", "partial", "failed", "complete", "restored"] = "running"
    files: list[FileChange]
    directories: list[CreatedDirectory] = Field(default_factory=list)
    requested_directories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_files(self) -> Self:
        if len({row.relative for row in self.files}) != len(self.files):
            raise ValueError("Duplicate operation targets")
        return self


class Ownership(ReceiptRecord):
    schema_version: Literal[1] = 1
    files: dict[str, Digest] = Field(default_factory=dict)
    last_operation: RunId | None = None
    publishing: dict[str, RunId] = Field(default_factory=dict)


def rollback_status(row: FileChange, current: FileState | None, *, published: bool) -> str:
    if current == row.before:
        return "unchanged"
    if not published or current != row.after:
        return "conflict"
    return "would-remove" if row.before is None else "would-restore"
