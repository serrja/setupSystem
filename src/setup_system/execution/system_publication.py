"""Conditionally replace a small generated system file and retain its displaced inode."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from setup_system.execution.atomic_rename import move_new
from setup_system.execution.system_files import (
    ensure_system_parent,
    set_new_directory_mode,
    validate_system_path,
)


@dataclass(frozen=True)
class ObservedFile:
    data: bytes
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    access_acl: bytes | None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


_ACCESS_ACL = "system.posix_acl_access"


def _access_acl(descriptor: int) -> bytes | None:
    try:
        return os.getxattr(descriptor, _ACCESS_ACL)
    except OSError as exc:
        if exc.errno in {errno.ENODATA, errno.ENOTSUP}:
            return None
        raise


def observe_file(path: Path) -> ObservedFile | None:
    validate_system_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        first = os.fstat(descriptor)
        if (
            not stat.S_ISREG(first.st_mode)
            or first.st_nlink != 1
            or first.st_uid != os.geteuid()
            or first.st_mode & 0o022
            or first.st_size > 1024 * 1024
        ):
            raise ValueError(f"Unsafe generated system file: {path}")
        data = stream.read(1024 * 1024 + 1)
        acl = _access_acl(descriptor)
        last = os.fstat(descriptor)
        if (
            len(data) != first.st_size
            or first.st_ctime_ns != last.st_ctime_ns
            or not os.path.samestat(last, path.lstat())
        ):
            raise ValueError(f"System file changed during inspection: {path}")
        return ObservedFile(
            data,
            first.st_dev,
            first.st_ino,
            first.st_uid,
            first.st_gid,
            stat.S_IMODE(first.st_mode),
            acl,
        )


def _directory(path: Path) -> None:
    validate_system_path(path, is_directory=True)


def publish_file(
    path: Path, data: bytes, before: ObservedFile | None, *, mode: int, parent_mode: int
) -> Path | None:
    """Publish a prepared inode only against the observed bytes, identity and metadata."""
    if observe_file(path) != before:
        raise ValueError(f"System file changed before publication: {path}")
    if before is not None and before.data == data and before.mode == mode:
        return None
    ensure_system_parent(path, parent_mode)
    area = Path(tempfile.mkdtemp(prefix=f".setup-system-{path.name}-", dir=path.parent))
    set_new_directory_mode(area, 0o700)
    prepared, original = area / "prepared", area / "original"
    candidate: ObservedFile | None = None
    try:
        with prepared.open("xb") as stream:
            if before is not None:
                os.fchown(stream.fileno(), before.uid, before.gid)
            if before is not None and before.access_acl is not None:
                os.setxattr(stream.fileno(), _ACCESS_ACL, before.access_acl)
            elif _access_acl(stream.fileno()) is not None:
                os.removexattr(stream.fileno(), _ACCESS_ACL)
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        candidate = observe_file(prepared)
        if candidate is None:
            raise ValueError("Prepared system file disappeared")
        if before is not None:
            move_new(path, original, check_directory=_directory)
            if observe_file(original) != before:
                raise ValueError(f"System file changed during publication: {path}")
        try:
            move_new(prepared, path, check_directory=_directory)
        except FileExistsError:
            raise ValueError(
                f"System file appeared during publication: {path}; inspect {area}"
            ) from None
        if observe_file(path) != candidate:
            raise ValueError(f"System file changed after publication: {path}; inspect {area}")
        if before is not None and observe_file(original) != before:
            # A writer can still edit the displaced inode through an open descriptor.
            # Reclaim only our candidate; another arrival keeps the live name.
            reclaimed = area / "installed"
            move_new(path, reclaimed, check_directory=_directory)
            try:
                ours = observe_file(reclaimed) == candidate
            except (OSError, ValueError):
                ours = False
            if not ours:
                move_new(reclaimed, path, check_directory=_directory)
            raise ValueError(
                f"Original system file changed during publication: {path}; inspect {area}"
            )
        return area if before is not None else None
    except BaseException:
        if os.path.lexists(original) and not os.path.lexists(path):
            try:
                move_new(original, path, check_directory=_directory)
            except FileExistsError:
                raise ValueError(
                    f"Concurrent publication conflict at {path}; inspect {area}"
                ) from None
        raise
    finally:
        if not os.path.lexists(original):
            if candidate is not None and observe_file(prepared) == candidate:
                prepared.unlink()
            if not any(area.iterdir()):
                area.rmdir()


def save_backup(path: Path, before: ObservedFile) -> None:
    """A content-addressed backup is write-once, including on retries."""
    saved = observe_file(path)
    if saved is not None:
        if saved.data != before.data or saved.mode != 0o600:
            raise ValueError(f"Existing system backup requires review: {path}")
        return
    publish_file(path, before.data, None, mode=0o600, parent_mode=0o700)
