"""Inspect a managed operation or restore its verified files under one writer lock."""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from setup_system_sdk.enums.resources import ReceiptState, RecoveryStep
from setup_system_sdk.files import receipt_repository
from setup_system_sdk.files.receipts import Operation
from setup_system_sdk.files.recovery_types import (
    RecoveryEntry,
    RecoveryFiles,
    RecoveryJournal,
    RecoveryTarget,
)
from setup_system_sdk.files.restore_files import LocalRecoveryFiles


def recover(
    folder: Path,
    *,
    apply: bool,
    journal: RecoveryJournal,
    files: RecoveryFiles,
    check: Callable[[Operation], None] | None = None,
) -> list[RecoveryEntry]:
    with journal.writer() if apply else nullcontext():
        operation = journal.load_operation(folder)
        if check:
            check(operation)
        if operation.status is ReceiptState.RESTORED:
            entries = [files.restored_entry(row) for row in operation.files]
            if apply:
                for entry in entries:
                    if entry.status is RecoveryStep.CONFLICT:
                        raise ValueError(f"Rollback conflicts with a later edit: {entry.path}")
            return entries
        targets = []
        for row in operation.files:
            entry = files.inspect(row)
            targets.append(RecoveryTarget(row, entry, journal.backup_bytes(folder, row)))
        directories = (
            [] if check else files.directories(operation, [target.entry.path for target in targets])
        )
        requested = set(operation.requested_directories)
        removable = files.removable_directories(
            directories,
            {
                target.entry.path
                for target in targets
                if target.entry.status is RecoveryStep.WOULD_REMOVE
            },
        )
        if not apply:
            return [
                *(target.entry for target in targets),
                *(
                    RecoveryEntry(path=path, status=RecoveryStep.WOULD_REMOVE_DIRECTORY)
                    for path, row in removable
                    if row.relative in requested
                ),
            ]
        for target in targets:
            if target.entry.status is RecoveryStep.CONFLICT:
                raise ValueError(files.conflict(target))
        if journal.load_operation(folder) != operation:
            raise ValueError("The operation receipt changed during recovery inspection")
        index = journal.ownership()
        results = []
        for target in targets:
            row = target.change
            status, original_matches = files.restore_target(target, operation, folder)
            name = str(target.entry.path)
            if row.previously_owned and row.before and original_matches:
                index.files[name] = row.before.sha256
            else:
                index.files.pop(name, None)
            if index.publishing.get(name) == operation.run_id:
                del index.publishing[name]
            results.append(RecoveryEntry(path=target.entry.path, status=status))
        eligible = {path for path, _ in removable}
        files.remove_directories(
            [
                (path, entry)
                for path, entry in directories
                if entry.relative not in requested or path in eligible
            ]
        )
        journal.save_ownership(index)
        operation.status = ReceiptState.RESTORED
        journal.save_operation(folder, operation)
        return results


def restore(backup_id: str, *, apply: bool = False) -> list[RecoveryEntry]:
    folder = receipt_repository.operation_folder(backup_id)
    return recover(folder, apply=apply, journal=receipt_repository, files=LocalRecoveryFiles())
