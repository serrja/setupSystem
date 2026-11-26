"""Atomically write bounded administrator-controlled system destinations."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def validate_system_path(path: Path, *, is_directory: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe system path")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("System path traverses a symlink")
    existing = path
    while not existing.exists():
        existing = existing.parent
    details = existing.stat()
    if details.st_uid != os.geteuid() or details.st_mode & 0o022:
        raise ValueError("System target/parent is not privately controlled by this user")
    if existing == path:
        if is_directory and not stat.S_ISDIR(details.st_mode):
            raise ValueError("System parent is not a directory")
        if not is_directory and (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1):
            raise ValueError("System file is not regular")
    directory = path.parent
    while not directory.exists():
        directory = directory.parent
    if os.geteuid() == 0:
        for parent in (directory, *directory.parents):
            info = parent.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
                raise ValueError("System ancestor is not administrator controlled")
    parent_details = directory.stat()
    if parent_details.st_uid != os.geteuid() or parent_details.st_mode & 0o022:
        raise ValueError("System parent is not privately controlled by this user")


def set_new_directory_mode(directory: Path, mode: int) -> None:
    # O_PATH also pins a directory created under umask 0777. Its procfs link
    # addresses the opened inode even if the pathname is subsequently replaced.
    descriptor = os.open(directory, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.chmod(f"/proc/self/fd/{descriptor}", mode)
        readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=descriptor)
        try:
            os.fsync(readable)
        finally:
            os.close(readable)
    finally:
        os.close(descriptor)


def ensure_system_parent(path: Path, parent_mode: int) -> None:
    validate_system_path(path)
    missing: list[Path] = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(mode=parent_mode)
        set_new_directory_mode(directory, parent_mode)
    validate_system_path(path)


def write_system(
    path: Path, content: bytes, mode: int = 0o600, *, parent_mode: int = 0o700
) -> None:
    validate_system_path(path)
    if path.exists():
        current = path.lstat()
        if (
            current.st_size == len(content)
            and stat.S_IMODE(current.st_mode) == mode
            and path.read_bytes() == content
        ):
            return
    ensure_system_parent(path, parent_mode)
    descriptor, name = tempfile.mkstemp(prefix=".setup-system-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(name).unlink(missing_ok=True)
