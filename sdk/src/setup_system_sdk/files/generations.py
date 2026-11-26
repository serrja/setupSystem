"""Verify sealed directory contents and hold generation leases."""

import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path


def bounded_file(path: Path, limit: int) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > limit:
            raise ValueError("Expected a bounded regular file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("File exceeds the size limit")
        return data


def private_storage(path: Path, *, create: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise ValueError("Plugin storage requires a real absolute directory")
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Unsafe plugin storage directory")


_SEAL = "generation.sha256"


@lru_cache(maxsize=4096)
def _file_digest(path: Path, device: int, inode: int, changed_ns: int, size: int) -> bytes:
    """Reuse file contents only while the kernel's inode identity and ctime match."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (
            info.st_dev,
            info.st_ino,
            info.st_ctime_ns,
            info.st_size,
        ) != (device, inode, changed_ns, size):
            raise ValueError("Extension changed while verifying its files")
        result = hashlib.file_digest(stream, "sha256").digest()
        if os.fstat(fd).st_ctime_ns != changed_ns:
            raise ValueError("Extension changed during verification")
        return result


def fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path == folder / _SEAL:
            continue
        info = path.lstat()
        digest.update(
            os.fsencode(path.relative_to(folder)) + b"\0" + str(info.st_mode).encode() + b"\0"
        )
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            digest.update(
                _file_digest(path, info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size)
            )
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("Extension contains an unexpected special file")
    return digest.hexdigest()


def verify_generation(folder: Path) -> None:
    if bounded_file(folder / _SEAL, 128).decode("ascii") != fingerprint(folder):
        raise ValueError("Extension files changed after installation")


def seal_generation(folder: Path) -> None:
    private_storage(folder)
    digest = fingerprint(folder)
    with (folder / _SEAL).open("x") as stream:
        stream.write(digest)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def generation_lock(folder: Path, *, exclusive: bool) -> Iterator[None]:
    fd = os.open(folder, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("The extension is in use") from None
        yield
    finally:
        os.close(fd)
