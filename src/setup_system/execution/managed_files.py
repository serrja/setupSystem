"""Apply reviewed user file changes with a private journal and verified publication."""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.execution.file_publication import (
    has_displaced_original,
    observe,
    prepared_target,
    publish_prepared,
    same_filesystem,
)
from setup_system.execution.receipt_repository import (
    load_operation,
    operation_folder,
    ownership,
    save_operation,
    save_ownership,
    writer,
)
from setup_system.execution.receipts import (
    CreatedDirectory,
    FileChange,
    FileState,
    Operation,
    Ownership,
    digest,
)
from setup_system.execution.user_storage import (
    atomic_write,
    checked_directory,
    private_directory,
    state_root,
    target_path,
)
from setup_system_sdk.enums.resources import ReceiptState


def require_recovered(index: Ownership, targets: Iterable[Path]) -> None:
    """Keep unrelated files usable while a displaced file awaits recovery."""
    for path in targets:
        run_id = index.publishing.get(str(path))
        if run_id is None:
            continue
        operation = load_operation(operation_folder(run_id))
        relative = path.relative_to(Path.home().resolve()).as_posix()
        row = next((row for row in operation.files if row.relative == relative), None)
        if row is None or has_displaced_original(row, path):
            raise ValueError(
                f"Restore interrupted file publication {run_id} before applying again: {path}"
            )


def _changes(
    desired: Mapping[Path, str],
    modes: Mapping[Path, int],
    expected: Mapping[Path, str | FileState | None],
    index: Ownership,
) -> list[FileChange]:
    changes = []
    for path, checksum in desired.items():
        relative = path.relative_to(Path.home().resolve()).as_posix()
        target_path(relative)
        before = observe(path)
        current = before.sha256 if before else None
        observed = before if isinstance(expected.get(path), FileState) else current
        if path in expected and observed != expected[path]:
            raise ValueError(f"Configuration changed since inspection: {path}")
        mode = modes.get(path, before.mode if before else 0o644)
        if mode < 0 or mode & ~0o755 or mode & 0o022:
            raise ValueError(f"Unsafe managed file mode: {path}")
        parent = next(p for p in path.parents if p.is_dir())
        inherited_gid = parent.stat().st_gid if parent.stat().st_mode & 0o2000 else os.getegid()
        after = FileState(
            sha256=checksum,
            uid=os.getuid(),
            gid=before.gid if before else inherited_gid,
            mode=mode,
        )
        if before == after:
            continue
        owned = before is not None and index.files.get(str(path)) == current
        if before is not None and not owned and path not in expected:
            raise ValueError(f"Locally edited/unmanaged configuration requires review: {path}")
        changes.append(
            FileChange(
                relative=relative,
                before=before,
                after=after,
                backup="files/" + relative if before else None,
                previously_owned=owned,
            )
        )
    return changes


def changed_digests(
    desired: Mapping[Path, str],
    *,
    modes: Mapping[Path, int] | None = None,
    expected: Mapping[Path, str | FileState | None] | None = None,
) -> tuple[Path, ...]:
    """Compare release digests with actual file state without fetching or writing."""
    rows = _changes(desired, modes or {}, expected or {}, ownership())
    return tuple(Path.home().resolve() / row.relative for row in rows)


def changed_files(
    desired: Mapping[Path, bytes],
    *,
    modes: Mapping[Path, int] | None = None,
    expected: Mapping[Path, str | FileState | None] | None = None,
) -> tuple[Path, ...]:
    return changed_digests(
        {p: digest(data) for p, data in desired.items()}, modes=modes, expected=expected
    )


def _create_parents(path: Path, operation: Operation, folder: Path) -> None:
    missing = []
    for parent in path.parents:
        if parent.exists():
            break
        missing.append(parent)
    for parent in reversed(missing):
        checked_directory(parent)
        parent.mkdir(mode=0o700)
        info = parent.stat()
        operation.directories.append(
            CreatedDirectory(
                relative=parent.relative_to(Path.home().resolve()).as_posix(),
                device=info.st_dev,
                inode=info.st_ino,
            )
        )
        save_operation(folder, operation)


def _prepare_publication(
    row: FileChange, path: Path, content: bytes, folder: Path, number: int
) -> None:
    if same_filesystem(path.parent, folder):
        root = folder / "publication"
        root.mkdir(mode=0o700, exist_ok=True)
        area = root / str(number)
        area.mkdir(mode=0o700)
    else:
        area = Path(tempfile.mkdtemp(prefix=".setup-system-apply-", dir=path.parent))
    details = area.stat()
    row.apply_directory = CreatedDirectory(
        relative=area.relative_to(Path.home().resolve()).as_posix(),
        device=details.st_dev,
        inode=details.st_ino,
    )
    prepared = area / "prepared"
    atomic_write(prepared, content, row.after.mode, preserve_existing_mode=False)
    if prepared.stat().st_gid != row.after.gid:
        os.chown(prepared, -1, row.after.gid)
    row.prepared_inode = prepared.stat().st_ino


def _publish_changes(changes: list[FileChange], record_write: Callable[[Path], None]) -> None:
    for row in changes:
        record_write(publish_prepared(row))


def apply_files(
    desired: Mapping[Path, bytes],
    *,
    directories: tuple[Path, ...] = (),
    modes: Mapping[Path, int] | None = None,
    expected: Mapping[Path, str | FileState | None] | None = None,
) -> Path:
    """Write managed files, or merge confirmed inputs guarded by their exact before hashes."""
    if os.geteuid() == 0:
        raise ValueError("User configuration and Ansible cannot run as root")
    modes = modes or {}
    expected = expected or {}
    if not (modes.keys() | expected.keys()) <= desired.keys():
        raise ValueError("Expected state must belong to a selected target")
    for path in directories:
        checked_directory(path)
        target_path((path / ".setup-system-dir").relative_to(Path.home().resolve()).as_posix())
    digests = {path: digest(data) for path, data in desired.items()}
    index = ownership()
    require_recovered(index, desired)
    changes = _changes(digests, modes, expected, index)
    if not changes and all(p.exists() for p in directories):
        return (
            state_root() / "backups" / index.last_operation
            if index.last_operation
            else state_root()
        )
    with writer():
        index = ownership()
        require_recovered(index, desired)
        changes = _changes(digests, modes, expected, index)
        if not changes and all(p.exists() for p in directories):
            return state_root()
        backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        folder = state_root() / "backups" / backup_id
        with private_directory(folder.parent) as parent:
            os.mkdir(backup_id, 0o700, dir_fd=parent)
            os.fsync(parent)
        op = Operation(
            run_id=backup_id,
            files=changes,
            requested_directories=[
                p.relative_to(Path.home().resolve()).as_posix() for p in directories
            ],
        )
        changes = op.files
        for row in changes:
            if row.backup is not None and row.before is not None:
                path = target_path(row.relative)
                original = path.read_bytes()
                if digest(original) != row.before.sha256 or observe(path) != row.before:
                    raise ValueError(f"File changed during backup: {path}")
                atomic_write(folder / row.backup, original)
        save_operation(folder, op)
        by_path = {target_path(row.relative): row for row in changes}

        def record_write(path: Path) -> None:
            row = by_path.get(path)
            if row and observe(path) == row.after:
                row.verified = True
                save_operation(folder, op)
                index.files[str(path)] = row.after.sha256
                index.last_operation = backup_id
                save_ownership(index)

        rc = -1
        failure: Exception | None = None
        try:
            for number, row in enumerate(changes):
                path = target_path(row.relative)
                _create_parents(path, op, folder)
                _prepare_publication(row, path, desired[path], folder, number)
                save_operation(folder, op)
            index.last_operation = backup_id
            index.publishing.update(
                {str(path): backup_id for path, row in by_path.items() if row.before}
            )
            save_ownership(index)
            for directory in directories:
                _create_parents(directory / ".setup-system-dir", op, folder)
            _publish_changes(changes, record_write)
            rc = 0
        except Exception as exc:
            failure = exc
        finally:
            op.status = ReceiptState.PARTIAL
            save_operation(folder, op)
            displaced = False
            for row in changes:
                try:
                    path = target_path(row.relative, validate_file=False)
                    if has_displaced_original(row, path):
                        displaced = True
                    elif index.publishing.get(str(path)) == backup_id:
                        del index.publishing[str(path)]
                    row.verified = (row.verified or prepared_target(row, path)) and observe(
                        path
                    ) == row.after
                except (ValueError, OSError):
                    row.verified = False
                    continue
                if row.verified:
                    index.files[str(path)] = row.after.sha256
            index.last_operation = backup_id
            save_ownership(index)
            if rc == 0 and all(row.verified for row in changes):
                op.status = ReceiptState.COMPLETE
            elif not any(row.verified for row in changes) and not displaced:
                op.status = ReceiptState.FAILED
            save_operation(folder, op)
        if op.status is ReceiptState.PARTIAL:
            reason = f": {error_text(failure)}" if failure else ""
            raise PartialApplyError(
                f"User configuration partially applied{reason}; inspect backup {backup_id}"
            ) from failure
        if failure is not None:
            raise failure
        if op.status is ReceiptState.FAILED:
            raise RuntimeError(f"User configuration failed readback; inspect backup {backup_id}")
        return folder
