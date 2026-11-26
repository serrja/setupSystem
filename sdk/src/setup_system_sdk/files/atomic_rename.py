"""Publish within pinned Linux directories only when the new name is unoccupied."""

import ctypes
import os
from collections.abc import Callable
from pathlib import Path


def _rename(
    source_fd: int, source_name: str, destination_fd: int, destination_name: str, flag: int
) -> None:
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(
        source_fd, os.fsencode(source_name), destination_fd, os.fsencode(destination_name), flag
    ):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination_name)


def rename_new(
    source_fd: int, source_name: str, destination_fd: int, destination_name: str
) -> None:
    """Move an entry without replacing another writer's destination."""
    _rename(source_fd, source_name, destination_fd, destination_name, 1)


def exchange(parent: int, staged: str, target: str) -> None:
    """Exchange two existing names and retain both inodes."""
    _rename(parent, staged, parent, target, 2)


def move_new(source: Path, destination: Path, *, check_directory: Callable[[Path], None]) -> None:
    """Pin both validated parents across the no-replace rename and directory fsync."""

    def open_directory(path: Path) -> int:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Publication requires an absolute controlled directory")
        check_directory(path)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        try:
            for name in path.parts[1:]:
                child = os.open(name, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            # The pinned inode must be the one covered by the owner's policy.
            check_directory(path)
            if not os.path.samestat(path.lstat(), os.fstat(descriptor)):
                raise ValueError("Publication directory changed during observation")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    origin = open_directory(source.parent)
    try:
        target = open_directory(destination.parent)
        try:
            rename_new(origin, source.name, target, destination.name)
            os.fsync(origin)
            os.fsync(target)
        finally:
            os.close(target)
    finally:
        os.close(origin)
