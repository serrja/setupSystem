"""Local filesystem adapter for read-only inspection and journaled compare-and-swap recovery."""

from __future__ import annotations

import errno
import os
import tempfile
from collections.abc import Collection
from pathlib import Path

from setup_system.domain.receipts import (
    RUN_ID,
    CreatedDirectory,
    FileChange,
    FileState,
    Operation,
    rollback_status,
)
from setup_system.domain.recovery import RecoveryTarget
from setup_system.infrastructure import file_publication
from setup_system.infrastructure.file_publication import (
    apply_stage,
    observe,
    prepared_target,
    same_filesystem,
)
from setup_system.infrastructure.receipt_repository import save_operation
from setup_system.infrastructure.user_storage import (
    atomic_write,
    checked_directory,
    safe_file,
    state_root,
    target_path,
)


def _retire_stage(row: FileChange, path: Path, operation: Operation, folder: Path) -> None:
    area = apply_stage(row, path)
    if area is None or os.path.lexists(area / "original"):
        return
    prepared = area / "prepared"
    if prepared_target(row, prepared) and observe(prepared) == row.after:
        prepared.unlink()
    if any(area.iterdir()):
        raise ValueError(f"Unrecognized publication evidence retained at {area}")
    # The journal keeps the directory identity until the empty directory is gone.
    area.rmdir()
    row.apply_directory = None
    row.prepared_inode = None
    save_operation(folder, operation)


def _interrupted_apply(row: FileChange, path: Path) -> tuple[Path, str] | None:
    area = apply_stage(row, path)
    if area is None:
        return None
    original = area / "original"
    if not original.exists() and not original.is_symlink():
        return None
    if (
        (area / "prepared").exists()
        and not path.exists()
        and not path.is_symlink()
        and row.restore_directory is None
    ):
        return original, "would-return-displaced"
    if observe(original) != row.before:
        return original, "conflict"
    return None


def _holding(record: CreatedDirectory | None, target: Path) -> Path | None:
    if record is None:
        return None
    folder = checked_directory(Path.home().resolve() / record.relative)
    root = folder.parent
    private = (
        root.name == "restored"
        and root.parent.parent == state_root() / "backups"
        and RUN_ID.fullmatch(root.parent.name)
    )
    if not folder.name.startswith(".setup-system-restore-") or not (
        private or root in target.parents
    ):
        raise ValueError("Invalid restore staging directory")
    if not folder.exists():
        return None
    info = folder.stat()
    if (info.st_dev, info.st_ino) != (record.device, record.inode) or info.st_mode & 0o077:
        raise ValueError("Restore staging directory changed since interruption")
    return folder


def _status(row: FileChange, path: Path) -> str:
    interrupted = _interrupted_apply(row, path)
    if interrupted is not None:
        return interrupted[1]
    if prepared_target(row, path) and observe(path) == row.after:
        row.verified = True
    holding = _holding(row.restore_directory, path)
    if holding is not None and (holding / "after").exists():
        if observe(holding / "after") != row.after:
            return "conflict"
        if not path.exists():
            return "would-remove" if row.before is None else "would-restore"
        # Older receipts can have both names after an interrupted link-based restore.
        staged = holding / "before"
        if (
            staged.is_file()
            and not staged.is_symlink()
            and not path.is_symlink()
            and staged.samefile(path)
        ):
            return "would-restore"
        return "unchanged" if observe(path) == row.before else "conflict"
    return rollback_status(row, observe(path))


def _publish_original(path: Path, staged: Path, content: bytes, state: FileState) -> None:
    if not path.exists():
        safe_file(staged, os.getuid())
        atomic_write(staged, content, state.mode, preserve_existing_mode=False)
        if staged.stat().st_gid != state.gid:
            os.chown(staged, -1, state.gid)
        try:
            file_publication.move_new(staged, path)
        except FileExistsError:
            raise ValueError(f"File appeared during restore: {path}") from None
    if (
        staged.exists()
        and not staged.is_symlink()
        and not path.is_symlink()
        and staged.samefile(path)
    ):
        staged.unlink()


def _restore_file(
    row: FileChange, path: Path, content: bytes | None, operation: Operation, folder: Path
) -> str:
    status = _status(row, path)
    if status == "conflict":
        raise ValueError(f"Rollback conflicts with a later edit: {path}")
    holding = _holding(row.restore_directory, path)
    if status == "unchanged" and holding is None:
        return status
    if holding is None:
        if same_filesystem(path.parent, folder):
            stable = folder / "restored"
            checked_directory(stable).mkdir(mode=0o700, exist_ok=True)
        else:
            created = {Path.home().resolve() / d.relative for d in operation.directories}
            stable = next(p for p in path.parents if p not in created)
            checked_directory(stable)
            if not same_filesystem(stable, path.parent):
                raise ValueError(f"Missing stable same-filesystem recovery directory for {path}")
        holding = Path(tempfile.mkdtemp(prefix=".setup-system-restore-", dir=stable))
        info = holding.stat()
        row.restore_directory = CreatedDirectory(
            relative=holding.relative_to(Path.home().resolve()).as_posix(),
            device=info.st_dev,
            inode=info.st_ino,
        )
        save_operation(folder, operation)
    displaced = holding / "after"
    if status != "unchanged" and not displaced.exists():
        # Move the actual incumbent aside before deciding whether it can be
        # replaced. A concurrent editor's version is never overwritten.
        file_publication.move_new(path, displaced)
        try:
            matches = observe(displaced) == row.after
        except (ValueError, OSError):
            matches = False
        if not matches:
            try:
                file_publication.move_new(displaced, path)
            except FileExistsError:
                raise ValueError(f"Concurrent edit retained at {displaced}") from None
            raise ValueError(f"Rollback conflicts with a later edit: {path}")
    if row.before is not None and status != "unchanged":
        assert content is not None
        _publish_original(path, holding / "before", content, row.before)
    if observe(path) != row.before:
        raise ValueError(f"Restore readback failed: {path}")
    if displaced.exists():
        # An editor may still hold this inode. Keep its name under the owner's
        # private recovery directory even after restoring the public path.
        row.retained_after = row.restore_directory
    else:
        holding.rmdir()
    row.restore_directory = None
    save_operation(folder, operation)
    return "removed" if row.before is None else "restored"


class LocalRecoveryFiles:
    def restored_entry(self, row: FileChange) -> tuple[str, str]:
        path = target_path(row.relative, validate_file=False)
        retained = _holding(row.retained_after, path)
        status = "already restored"
        if retained is not None:
            status += f"; displaced file retained at {retained / 'after'}"
        return str(path), status

    def inspect(self, row: FileChange) -> tuple[Path, str]:
        path = target_path(row.relative, validate_file=False)
        if row.after.uid != os.getuid() or (row.before and row.before.uid != os.getuid()):
            raise ValueError("Recovery owner differs from the current user")
        return path, _status(row, path)

    def directories(
        self, op: Operation, paths: Collection[Path]
    ) -> list[tuple[Path, CreatedDirectory]]:
        directories = []
        for entry in op.directories:
            path = target_path(entry.relative + "/.setup-system-directory-check").parent
            if entry.relative not in op.requested_directories and not any(
                path in p.parents for p in paths
            ):
                raise ValueError("Recovery directory has no managed files")
            if path.exists() and (path.stat().st_dev, path.stat().st_ino) == (
                entry.device,
                entry.inode,
            ):
                directories.append((path, entry))
        return directories

    def conflict(self, target: RecoveryTarget) -> str:
        area = apply_stage(target.change, target.path)
        retained = (
            f"; review displaced file in {area}" if area and (area / "original").exists() else ""
        )
        return f"Rollback conflicts with a later edit: {target.path}{retained}"

    def restore_target(
        self, target: RecoveryTarget, operation: Operation, folder: Path
    ) -> tuple[str, bool]:
        row, path, status = target.change, target.path, target.status
        if status != _status(row, path):
            raise ValueError(f"File changed during restore: {path}")
        if status == "would-return-displaced":
            interrupted = _interrupted_apply(row, path)
            if interrupted is None:
                raise ValueError(f"Displaced file changed before restore: {path}")
            file_publication.move_new(interrupted[0], path)
            status = "returned displaced file"
        else:
            status = _restore_file(row, path, target.original, operation, folder)
        _retire_stage(row, path, operation, folder)
        return status, bool(row.previously_owned and row.before and observe(path) == row.before)

    def remove_directories(self, entries: list[tuple[Path, CreatedDirectory]]) -> None:
        for path, entry in sorted(entries, key=lambda item: len(item[0].parts), reverse=True):
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == (entry.device, entry.inode):
                    path.rmdir()
            except OSError as exc:
                if exc.errno not in {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST}:
                    raise
