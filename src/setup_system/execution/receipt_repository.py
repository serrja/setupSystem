"""Persist managed-file receipts under the native writer lock."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from setup_system.execution.receipts import (
    RUN_ID,
    FileChange,
    Operation,
    Ownership,
    ReceiptRecord,
    digest,
)
from setup_system.execution.user_storage import (
    atomic_write,
    capture_receipt,
    checked_directory,
    private_directory,
    safe_file,
    state_root,
)
from setup_system_sdk.enums.resources import ReceiptState


def write_record(path: Path, record: ReceiptRecord) -> None:
    record = type(record).model_validate(record)
    safe_file(path, os.getuid())
    atomic_write(
        path,
        (record.model_dump_json(indent=2) + "\n").encode(),
        0o600,
        preserve_existing_mode=False,
    )


def ownership() -> Ownership:
    path = checked_directory(state_root()) / "workstation.json"
    safe_file(path, os.getuid())
    if not path.exists():
        return Ownership()
    return Ownership.model_validate(capture_receipt(path).json())


def operation_folder(backup_id: str) -> Path:
    if not RUN_ID.fullmatch(backup_id):
        raise ValueError("Invalid backup ID")
    return checked_directory(state_root() / "backups" / backup_id)


@contextmanager
def writer() -> Iterator[None]:
    if os.geteuid() == 0:
        raise ValueError("User configuration and Ansible cannot run as root")
    with private_directory(state_root()) as descriptor:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another user configuration operation is running") from None
        yield


def list_operations() -> list[tuple[str, ReceiptState]]:
    """Read only native file receipts in this runtime's private backup directory."""
    directory = checked_directory(state_root() / "backups")
    if not directory.exists():
        return []
    records: list[tuple[str, ReceiptState]] = []
    for entry in sorted(directory.iterdir(), reverse=True):
        if RUN_ID.fullmatch(entry.name):
            operation = load_operation(operation_folder(entry.name))
            records.append((operation.run_id, operation.status))
    return records


def receipt_source(folder: Path) -> Path | None:
    path = folder / "operation.json"
    safe_file(path, os.getuid())
    if path.is_file():
        return path
    return None


def load_operation(folder: Path) -> Operation:
    path = receipt_source(folder)
    if path is None:
        raise ValueError("Managed file operation receipt is missing")
    operation = Operation.model_validate_json(capture_receipt(path).data)
    if folder.name != operation.run_id:
        raise ValueError("Invalid or duplicate operation targets")
    return operation


def backup_bytes(folder: Path, row: FileChange) -> bytes | None:
    if row.backup is None or row.before is None:
        return None
    backup = folder / row.backup
    checked_directory(backup.parent)
    safe_file(backup, os.getuid())
    content = backup.read_bytes()
    if digest(content) != row.before.sha256:
        raise ValueError(f"Backup hash mismatch: {row.relative}")
    return content


def save_operation(folder: Path, operation: Operation) -> None:
    if folder.name != operation.run_id:
        raise ValueError("Operation ID mismatch")
    write_record(folder / "operation.json", operation)


def save_ownership(index: Ownership) -> None:
    write_record(state_root() / "workstation.json", index)
