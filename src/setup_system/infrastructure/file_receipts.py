"""Versioned recovery evidence for unprivileged managed files."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from setup_system.infrastructure.config_store import safe_file
from setup_system.storage import atomic_write, state_root

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FileState(Record):
    sha256: Digest
    mode: int = Field(ge=0, le=0o777)
    uid: int = Field(ge=0)
    gid: int = Field(ge=0)


class CreatedDirectory(Record):
    relative: str
    device: int
    inode: int


class FileChange(Record):
    relative: str
    before: FileState | None
    backup: str | None
    previously_owned: bool
    after: FileState
    verified: bool = False
    restore_directory: CreatedDirectory | None = None
    apply_directory: CreatedDirectory | None = None
    prepared_inode: int | None = None

    @model_validator(mode="after")
    def validate_backup(self) -> Self:
        expected = "files/" + self.relative if self.before is not None else None
        if self.backup != expected:
            raise ValueError("Backup does not match recorded original file")
        return self


class Operation(Record):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=RUN_ID.pattern)
    status: Literal["running", "partial", "failed", "complete", "restored"] = "running"
    files: list[FileChange]
    directories: list[CreatedDirectory] = Field(default_factory=list)
    requested_directories: list[str] = Field(default_factory=list)

    def save(self, folder: Path) -> None:
        if folder.name != self.run_id:
            raise ValueError("Operation ID mismatch")
        write_record(folder / "operation.json", self)


class Ownership(Record):
    schema_version: Literal[1] = 1
    files: dict[str, Digest] = Field(default_factory=dict)
    last_operation: Annotated[str, Field(pattern="^" + RUN_ID.pattern + "$")] | None = None
    publishing: dict[str, Annotated[str, Field(pattern="^" + RUN_ID.pattern + "$")]] = Field(
        default_factory=dict
    )

    def save(self) -> None:
        write_record(state_root() / "workstation.json", self)


def write_record(path: Path, record: Record) -> None:
    safe_file(path, os.getuid())
    atomic_write(
        path,
        (record.model_dump_json(indent=2) + "\n").encode(),
        0o600,
        preserve_existing_mode=False,
    )


def checked_directory(path: Path) -> Path:
    home = Path.home().resolve()
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(home):
        raise ValueError(f"Managed path must stay inside HOME: {path}")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"Managed path traverses a symlink: {parent}")
        if parent.exists():
            info = parent.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError(f"Unsafe owner or permissions on directory: {parent}")
        if parent == home:
            break
    return path


def target_path(relative: str, *, validate_file: bool = True) -> Path:
    path = Path(relative)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise ValueError(f"Invalid relative managed path: {relative}")
    result = Path.home().resolve() / path
    if result.is_relative_to(state_root()):
        raise ValueError("Recovery metadata cannot be a managed target")
    checked_directory(result.parent)
    if validate_file:
        safe_file(result, os.getuid())
    return result


def observe(path: Path) -> FileState | None:
    safe_file(path, os.getuid())
    if not path.exists():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o7022
        ):
            raise ValueError(f"Invalid managed file owner, links or permissions: {path}")
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return FileState(
        sha256=checksum, mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid
    )


def _backup_original(path: Path) -> None:
    backup = path.with_name(path.stem + ".v0.json")
    safe_file(backup, os.getuid())
    data = path.read_bytes()
    if backup.exists() and backup.read_bytes() != data:
        raise ValueError(f"Conflicting previous migration backup: {backup}")
    atomic_write(backup, data)


def ownership(*, migrate: bool = False) -> Ownership:
    path = checked_directory(state_root()) / "workstation.json"
    safe_file(path, os.getuid())
    if not path.is_file():
        return Ownership()
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("Unknown ownership receipt format")
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
    index = Ownership(files=data, last_operation=Path(last).name if last else None)
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
    directory = checked_directory(state_root())
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another user configuration operation is running") from None
        yield
    finally:
        os.close(descriptor)


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


def read_object(path: Path) -> dict[str, object]:
    safe_file(path, os.getuid())
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("File receipt exceeds the size limit")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("Invalid file receipt")
    return data


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
        from setup_system.infrastructure.legacy_receipts import migrate_user_files

        try:
            operation = migrate_user_files(folder, data, path)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid v0.4.0 file receipt") from exc
        if migrate:
            if path.name == "operation.json":
                _backup_original(path)
            operation.save(folder)
    if folder.name != operation.run_id or len({row.relative for row in operation.files}) != len(
        operation.files
    ):
        raise ValueError("Invalid or duplicate operation targets")
    return operation


def _backup_bytes(folder: Path, row: FileChange) -> bytes | None:
    if row.backup is None or row.before is None:
        return None
    backup = folder / row.backup
    checked_directory(backup.parent)
    safe_file(backup, os.getuid())
    content = backup.read_bytes()
    if digest(content) != row.before.sha256:
        raise ValueError(f"Backup hash mismatch: {row.relative}")
    return content


def _rollback_status(row: FileChange, current: FileState | None) -> str:
    if current == row.before:
        return "unchanged"
    if not row.verified or current != row.after:
        return "conflict"
    return "would-remove" if row.before is None else "would-restore"


@contextmanager
def _directory_fd(directory: Path) -> Iterator[int]:
    """Pin each parent without following symlinks, including during a concurrent rename."""
    checked_directory(directory)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        home_depth = len(Path.home().resolve().parts)
        for depth, name in enumerate(directory.parts[1:], start=2):
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if depth >= home_depth and (info.st_uid != os.getuid() or info.st_mode & 0o022):
                raise ValueError(f"Unsafe parent during publication: {directory}")
        yield fd
    finally:
        os.close(fd)


def _move_new(source: Path, destination: Path) -> None:
    """Move within one Linux filesystem only when the destination name is free."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    with _directory_fd(source.parent) as source_fd, _directory_fd(destination.parent) as dest_fd:
        # RENAME_NOREPLACE from the Linux UAPI.
        if (
            rename(source_fd, os.fsencode(source.name), dest_fd, os.fsencode(destination.name), 1)
            != 0
        ):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
        os.fsync(source_fd)
        os.fsync(dest_fd)


def apply_stage(row: FileChange, path: Path) -> Path | None:
    record = row.apply_directory
    if record is None:
        return None
    area = checked_directory(Path.home().resolve() / record.relative)
    backup = state_root() / "backups"
    private = (
        area.parent.name == "publication"
        and area.parent.parent.parent == backup
        and RUN_ID.fullmatch(area.parent.parent.name)
    )
    if not private and not (
        area.parent == path.parent and area.name.startswith(".setup-system-apply-")
    ):
        raise ValueError("Invalid publication staging directory")
    if not area.exists() and (row.verified or observe(path) == row.before):
        return None
    info = area.stat()
    if (info.st_dev, info.st_ino) != (record.device, record.inode) or info.st_mode & 0o077:
        raise ValueError("Publication staging directory changed")
    return area


def prepared_target(row: FileChange, path: Path) -> bool:
    stage = row.apply_directory
    if stage is None or not path.exists() or path.is_symlink():
        return False
    info = path.stat()
    return (info.st_dev, info.st_ino) == (stage.device, row.prepared_inode)


def publish_prepared(row: FileChange) -> Path:
    """Ansible local module entry. Claim and check the actual displaced inode."""
    if os.geteuid() == 0:
        raise ValueError("Cannot publish user files as root")
    path = target_path(row.relative, validate_file=False)
    area = apply_stage(row, path)
    if area is None:
        raise ValueError("Publication evidence is missing")
    prepared, original = area / "prepared", area / "original"
    if not prepared_target(row, prepared) or observe(prepared) != row.after:
        raise ValueError("Prepared file identity or content changed")
    if row.before is not None:
        # Reserve the displaced inode in the journal even if its writer still
        # holds an open fd. The original remains available after a successful apply.
        _move_new(path, original)
        try:
            if observe(original) != row.before:
                raise ValueError(f"File changed before publication: {path}")
        except (ValueError, OSError):
            try:
                _move_new(original, path)
            except FileExistsError:
                raise ValueError(f"Concurrent edits retained at {path} and {original}") from None
            raise
    try:
        _move_new(prepared, path)
    except FileExistsError:
        raise ValueError(f"File appeared during publication: {path}; inspect {area}") from None
    if not prepared_target(row, path) or observe(path) != row.after:
        raise ValueError(f"File changed during publication: {path}; inspect {area}")
    return path


def has_displaced_original(row: FileChange, path: Path) -> bool:
    area = apply_stage(row, path)
    return bool(area and (area / "prepared").exists() and os.path.lexists(area / "original"))


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
    operation.save(folder)


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


def _holding(row: FileChange, target: Path) -> Path | None:
    record = row.restore_directory
    if record is None:
        return None
    folder = checked_directory(Path.home().resolve() / record.relative)
    if folder.parent != target.parent or not folder.name.startswith(".setup-system-restore-"):
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
    holding = _holding(row, path)
    if holding is not None and (holding / "after").exists():
        if observe(holding / "after") != row.after:
            return "conflict"
        if not path.exists():
            return "would-remove" if row.before is None else "would-restore"
        # Publication uses a no-replace hard link. A process interruption between
        # linking and unlinking the staged original can leave both names.
        staged = holding / "before"
        if (
            staged.is_file()
            and not staged.is_symlink()
            and not path.is_symlink()
            and staged.samefile(path)
        ):
            return "would-restore"
        return "unchanged" if observe(path) == row.before else "conflict"
    return _rollback_status(row, observe(path))


def _publish_original(path: Path, staged: Path, content: bytes, state: FileState) -> None:
    if not path.exists():
        safe_file(staged, os.getuid())
        atomic_write(staged, content, state.mode, preserve_existing_mode=False)
        if staged.stat().st_gid != state.gid:
            os.chown(staged, -1, state.gid)
        try:
            os.link(staged, path, follow_symlinks=False)
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
    holding = _holding(row, path)
    if status == "unchanged" and holding is None:
        return status
    if holding is None:
        holding = Path(tempfile.mkdtemp(prefix=".setup-system-restore-", dir=path.parent))
        info = holding.stat()
        row.restore_directory = CreatedDirectory(
            relative=holding.relative_to(Path.home().resolve()).as_posix(),
            device=info.st_dev,
            inode=info.st_ino,
        )
        operation.save(folder)
    displaced = holding / "after"
    if status != "unchanged" and not displaced.exists():
        # Move the actual incumbent aside before deciding whether it can be
        # replaced. A concurrent editor's version is never overwritten.
        os.rename(path, displaced)
        try:
            matches = observe(displaced) == row.after
        except (ValueError, OSError):
            matches = False
        if not matches:
            try:
                os.link(displaced, path, follow_symlinks=False)
            except FileExistsError:
                raise ValueError(f"Concurrent edit retained at {displaced}") from None
            displaced.unlink()
            raise ValueError(f"Rollback conflicts with a later edit: {path}")
    if row.before is not None and status != "unchanged":
        assert content is not None
        _publish_original(path, holding / "before", content, row.before)
    if observe(path) != row.before:
        raise ValueError(f"Restore readback failed: {path}")
    if displaced.exists():
        if observe(displaced) != row.after:
            raise ValueError(f"Concurrent edit retained at {displaced}")
        displaced.unlink()
    holding.rmdir()
    row.restore_directory = None
    operation.save(folder)
    return "removed" if row.before is None else "restored"


def restore_operation(folder: Path, *, apply: bool) -> list[tuple[str, str]]:
    if apply:
        with writer():
            return _restore_operation(folder, apply=True)
    return _restore_operation(folder, apply=False)


def _restore_operation(folder: Path, *, apply: bool) -> list[tuple[str, str]]:
    operation = load_operation(folder)
    if operation.status == "restored":
        return [
            (str(target_path(row.relative, validate_file=False)), "already restored")
            for row in operation.files
        ]
    rows = []
    for row in operation.files:
        path = target_path(row.relative, validate_file=False)
        if row.after.uid != os.getuid() or (row.before and row.before.uid != os.getuid()):
            raise ValueError("Recovery owner differs from the current user")
        status = _status(row, path)
        rows.append((row, path, status, _backup_bytes(folder, row)))
    directories = []
    for entry in operation.directories:
        path = target_path(entry.relative + "/.setup-system-directory-check").parent
        if entry.relative not in operation.requested_directories and not any(
            path in item[1].parents for item in rows
        ):
            raise ValueError("Recovery directory has no managed files")
        if path.exists() and (path.stat().st_dev, path.stat().st_ino) == (
            entry.device,
            entry.inode,
        ):
            directories.append((path, entry))
    if not apply:
        return [(str(path), status) for _, path, status, _ in rows]
    for row, path, status, _ in rows:
        if status == "conflict":
            area = apply_stage(row, path)
            retained = (
                f"; review displaced file in {area}"
                if area and (area / "original").exists()
                else ""
            )
            raise ValueError(f"Rollback conflicts with a later edit: {path}{retained}")
    load_operation(folder, migrate=True)
    index = ownership(migrate=True)
    results = []
    for row, path, status, content in rows:
        if status != _status(row, path):
            raise ValueError(f"File changed during restore: {path}")
        if status == "would-return-displaced":
            interrupted = _interrupted_apply(row, path)
            if interrupted is None:
                raise ValueError(f"Displaced file changed before restore: {path}")
            _move_new(interrupted[0], path)
            status = "returned displaced file"
        else:
            status = _restore_file(row, path, content, operation, folder)
        _retire_stage(row, path, operation, folder)
        if row.previously_owned and row.before and observe(path) == row.before:
            index.files[str(path)] = row.before.sha256
        else:
            index.files.pop(str(path), None)
        if index.publishing.get(str(path)) == operation.run_id:
            del index.publishing[str(path)]
        results.append((str(path), status))
    for path, entry in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
        try:
            info = path.lstat()
            if (info.st_dev, info.st_ino) == (entry.device, entry.inode):
                path.rmdir()
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST}:
                raise
    index.save()
    operation.status = "restored"
    operation.save(folder)
    return results
