"""Publish owned files with pinned parent descriptors and verified same-filesystem staging."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from setup_system.execution.atomic_rename import rename_new
from setup_system.execution.receipts import RUN_ID, FileChange, FileState
from setup_system.execution.user_storage import (
    checked_directory,
    safe_file,
    state_root,
    target_path,
)


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


def move_new(source: Path, destination: Path) -> None:
    """Move within one Linux filesystem only when the destination name is free."""
    with _directory_fd(source.parent) as source_fd, _directory_fd(destination.parent) as dest_fd:
        rename_new(source_fd, source.name, dest_fd, destination.name)
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
        move_new(path, original)
        try:
            if observe(original) != row.before:
                raise ValueError(f"File changed before publication: {path}")
        except (ValueError, OSError):
            try:
                move_new(original, path)
            except FileExistsError:
                raise ValueError(f"Concurrent edits retained at {path} and {original}") from None
            raise
    try:
        move_new(prepared, path)
    except FileExistsError:
        raise ValueError(f"File appeared during publication: {path}; inspect {area}") from None
    if not prepared_target(row, path) or observe(path) != row.after:
        raise ValueError(f"File changed during publication: {path}; inspect {area}")
    return path


def has_displaced_original(row: FileChange, path: Path) -> bool:
    area = apply_stage(row, path)
    return bool(area and (area / "prepared").exists() and os.path.lexists(area / "original"))


def same_filesystem(first: Path, second: Path) -> bool:
    return first.stat().st_dev == second.stat().st_dev
