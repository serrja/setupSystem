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
            return [files.restored_entry(row) for row in operation.files]
        targets = []
        for row in operation.files:
            path, status = files.inspect(row)
            targets.append(RecoveryTarget(row, path, status, journal.backup_bytes(folder, row)))
        directories = files.directories(operation, [target.path for target in targets])
        if not apply:
            return [(str(target.path), target.status) for target in targets]
        for target in targets:
            if target.status == "conflict":
                raise ValueError(files.conflict(target))
        journal.load_operation(folder, migrate=True)
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
        files.remove_directories(directories)
        journal.save_ownership(index)
        operation.status = "restored"
        journal.save_operation(folder, operation)
        return results
