"""Inspect a managed operation or restore its verified files under one writer lock."""

from contextlib import nullcontext
from pathlib import Path

from setup_system.domain.recovery import RecoveryFiles, RecoveryJournal, RecoveryTarget


def recover(
    folder: Path, *, apply: bool, journal: RecoveryJournal, files: RecoveryFiles
) -> list[tuple[str, str]]:
    with journal.writer() if apply else nullcontext():
        operation = journal.load_operation(folder)
        if operation.status == "restored":
            entries = [files.restored_entry(row) for row in operation.files]
            if apply:
                for name, status in entries:
                    if status == "conflict":
                        raise ValueError(f"Rollback conflicts with a later edit: {name}")
            return entries
        targets = []
        for row in operation.files:
            path, status = files.inspect(row)
            targets.append(RecoveryTarget(row, path, status, journal.backup_bytes(folder, row)))
        directories = files.directories(operation, [target.path for target in targets])
        requested = set(operation.requested_directories)
        removable = files.removable_directories(
            directories, {target.path for target in targets if target.status == "would-remove"}
        )
        if not apply:
            return [
                *((str(target.path), target.status) for target in targets),
                *(
                    (str(path), "would-remove-directory")
                    for path, row in removable
                    if row.relative in requested
                ),
            ]
        for target in targets:
            if target.status == "conflict":
                raise ValueError(files.conflict(target))
        if journal.load_operation(folder, migrate=True) != operation:
            raise ValueError("The operation receipt changed during recovery inspection")
        index = journal.ownership(migrate=True)
        results = []
        for target in targets:
            row = target.change
            status, original_matches = files.restore_target(target, operation, folder)
            name = str(target.path)
            if row.previously_owned and row.before and original_matches:
                index.files[name] = row.before.sha256
            else:
                index.files.pop(name, None)
            if index.publishing.get(name) == operation.run_id:
                del index.publishing[name]
            results.append((name, status))
        eligible = {path for path, _ in removable}
        files.remove_directories(
            [
                (path, entry)
                for path, entry in directories
                if entry.relative not in requested or path in eligible
            ]
        )
        journal.save_ownership(index)
        operation.status = "restored"
        journal.save_operation(folder, operation)
        return results
