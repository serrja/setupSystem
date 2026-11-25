"""Persist, enumerate and migrate versioned user-file operations under the writer lock."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from setup_system.domain.receipts import (
    RUN_ID,
    FileChange,
    Operation,
    Ownership,
    ReceiptRecord,
    digest,
)
from setup_system.infrastructure.atomic_rename import rename_new
from setup_system.infrastructure.receipt_migrations import migrate_user_files
from setup_system.infrastructure.user_storage import (
    atomic_write,
    checked_directory,
    private_directory,
    read_object,
    receipt_bytes,
    safe_file,
    staged_file,
    state_root,
    target_path,
)


def write_record(path: Path, record: ReceiptRecord) -> None:
    record = type(record).model_validate(record)
    safe_file(path, os.getuid())
    atomic_write(
        path,
        (record.model_dump_json(indent=2) + "\n").encode(),
        0o600,
        preserve_existing_mode=False,
    )


def _backup_original(path: Path) -> None:
    backup = path.with_name(path.stem + ".v0.json")
    data = receipt_bytes(path)
    with private_directory(path.parent, create=False) as parent:
        with staged_file(parent, backup.name, data, 0o600) as candidate:
            try:
                rename_new(parent, candidate, parent, backup.name)
            except FileExistsError:
                if receipt_bytes(backup) != data:
                    raise ValueError(f"Conflicting previous migration backup: {backup}") from None
            os.fsync(parent)


def ownership(*, migrate: bool = False) -> Ownership:
    path = checked_directory(state_root()) / "workstation.json"
    safe_file(path, os.getuid())
    if not path.is_file():
        return Ownership()
    data = read_object(path)
    if "schema_version" in data:
        return Ownership.model_validate(data)
    # v0.4.0 tracked absolute filenames, plus an optional private backup folder.
    last = data.pop("__backup__", None)
    if last is not None and (
        not isinstance(last, str)
        or Path(last).parent != state_root() / "backups"
        or not RUN_ID.fullmatch(Path(last).name)
    ):
        raise ValueError("Invalid legacy ownership receipt")
    for key in data:
        path_key = Path(key)
        relative = path_key.relative_to(Path.home().resolve()).as_posix()
        if target_path(relative) != path_key:
            raise ValueError("Invalid legacy managed path")
    index = Ownership.model_validate(
        {"files": data, "last_operation": Path(last).name if last else None}
    )
    if migrate:
        _backup_original(path)
    return index


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


def list_operations() -> list[tuple[str, str]]:
    base = checked_directory(state_root() / "backups")
    if not base.exists():
        return []
    results: list[tuple[str, str]] = []
    for item in sorted(base.iterdir(), reverse=True):
        if not RUN_ID.fullmatch(item.name):
            continue
        try:
            folder = operation_folder(item.name)
            source = receipt_source(folder)
            if source is None and not (folder / "manifest.json").is_file():
                continue
            status = "backup"
            if source is not None:
                data = read_object(source)
                if source.name == "operation.json" and "schema_version" in data:
                    status = Operation.model_validate(data).status
                else:
                    status = "legacy file operation"
        except (ValueError, OSError):
            status = "requires review"
        results.append((item.name, status))
    return results


def receipt_source(folder: Path) -> Path | None:
    path = folder / "operation.json"
    safe_file(path, os.getuid())
    if path.is_file():
        return path
    legacy = []
    for source in (
        folder / "workstation.json",
        folder / "git-profile.json",
        state_root() / "shell-applies" / (folder.name + ".json"),
        state_root() / "rtk-installs" / (folder.name + ".json"),
    ):
        checked_directory(source.parent)
        safe_file(source, os.getuid())
        if source.exists():
            legacy.append(source)
    if len(legacy) > 1:
        raise ValueError("Ambiguous v0.4.0 recovery evidence")
    return legacy[0] if legacy else None


def load_operation(folder: Path, *, migrate: bool = False) -> Operation:
    path = receipt_source(folder)
    if path is None:
        raise ValueError("File operation receipt is missing")
    data = read_object(path)
    if "schema_version" in data:
        if path.name != "operation.json":
            raise ValueError("Unknown legacy file receipt version")
        operation = Operation.model_validate(data)
    else:
        try:
            operation = migrate_user_files(folder, data, path)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid v0.4.0 file receipt") from exc
        if migrate:
            if path.name == "operation.json":
                _backup_original(path)
            save_operation(folder, operation)
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
