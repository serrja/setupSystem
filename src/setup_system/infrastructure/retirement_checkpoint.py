"""Complete retirement from a durable parent receipt after the public directory disappears."""

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from setup_system.infrastructure.atomic_rename import rename_new
from setup_system.infrastructure.recorded_tree import Entry, remove_recorded
from setup_system_sdk import Inputs

NAMESPACE = ".setup-system-retirements"
_DIRECTORY = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY
_MAX = 64 * 1024 * 1024


class Checkpoint(Inputs):
    target: str
    directory: tuple[int, int]
    marker: str
    entry: Entry
    payload: str = Field(repr=False)


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Retirement requires a real private directory")
    fd = os.open(path, _DIRECTORY)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("Unsafe retirement directory")
        yield fd
    finally:
        os.close(fd)


def _read(directory: int, name: str, limit: int) -> tuple[bytes, Entry]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(name, flags, dir_fd=directory), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > limit
        ):
            raise ValueError("Unsafe retirement receipt")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Retirement receipt exceeds its size limit")
        return data, (info.st_dev, info.st_ino, info.st_mode, hashlib.sha256(data).hexdigest())


def _publish(destination: int, name: str, data: bytes, staging: int) -> None:
    temporary = ".staged-" + uuid4().hex
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(temporary, flags, mode=0o600, dir_fd=staging)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        rename_new(staging, temporary, destination, name)
        os.fsync(destination)
        os.fsync(staging)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=staging)


def publish(directory: Path, name: str, data: bytes, private_parent: Path) -> None:
    """Stage outside the public tree, then atomically publish a single-link receipt."""
    with _directory(private_parent) as parent:
        with suppress(FileExistsError):
            os.mkdir(NAMESPACE, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    with _directory(private_parent / NAMESPACE) as staging, _directory(directory) as destination:
        _publish(destination, name, data, staging)


def targets(parent: Path) -> set[str]:
    directory = parent / NAMESPACE
    if not os.path.lexists(directory):
        return set()
    with _directory(directory) as fd:
        return {
            name.partition(".")[0]
            for name in os.listdir(fd)
            if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}[.](json|final)", name)
        }


def pending(root: Path) -> bool:
    return any(
        os.path.lexists(root.parent / NAMESPACE / (root.name + suffix))
        for suffix in (".json", ".final")
    )


def _receipt(root: Path) -> str:
    directory = root.parent / NAMESPACE
    names = [
        root.name + suffix
        for suffix in (".json", ".final")
        if os.path.lexists(directory / (root.name + suffix))
    ]
    if len(names) != 1:
        raise ValueError("Retirement receipt is missing or ambiguous")
    return names[0]


@contextmanager
def allow_creation(root: Path) -> Iterator[None]:
    """Serialize creation of a public directory with its final retirement."""
    with _directory(root.parent) as parent:
        fcntl.flock(parent, fcntl.LOCK_EX)
        if pending(root):
            raise ValueError("Finish the interrupted retirement before updating")
        yield


def _parse(root: Path, marker: str, data: bytes) -> Checkpoint:
    record = Checkpoint.model_validate_json(data)
    if (
        record.target != root.name
        or record.marker != marker
        or record.entry[3] != hashlib.sha256(record.payload.encode()).hexdigest()
    ):
        raise ValueError("Retirement receipt identity differs")
    return record


def load(root: Path, marker: str) -> Checkpoint:
    with _directory(root.parent / NAMESPACE) as directory:
        data, _ = _read(directory, _receipt(root), 2 * _MAX + 4096)
    return _parse(root, marker, data)


def check_target(root: Path, record: Checkpoint) -> None:
    """Keep an unrelated replacement visible as a conflict in the confirmation preview."""
    claim = root.parent / NAMESPACE / (root.name + ".directory")
    locations = [p for p in (root, claim) if os.path.lexists(p)]
    if len(locations) > 1:
        raise ValueError("A new directory appeared during retirement")
    if locations:
        with _directory(locations[0]) as opened:
            info = os.fstat(opened)
            if (info.st_dev, info.st_ino) != record.directory:
                raise ValueError("Retirement directory identity changed")


def _remove(root: Path, parent: int, directory: int, record: Checkpoint) -> None:
    claim = root.name + ".directory"
    storage = root.parent / NAMESPACE
    if os.path.lexists(root):
        if os.path.lexists(storage / claim):
            raise ValueError("A new directory appeared during retirement")
        rename_new(parent, root.name, directory, claim)
        os.fsync(parent)
        os.fsync(directory)
    if os.path.lexists(storage / claim):
        try:
            with _directory(storage / claim) as fd:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != record.directory:
                    raise ValueError("Retirement directory identity changed")
                remove_recorded(storage / claim, {record.marker: record.entry})
            os.rmdir(claim, dir_fd=directory)
            os.fsync(directory)
        except (ValueError, OSError):
            with suppress(FileExistsError, FileNotFoundError):
                rename_new(directory, claim, parent, root.name)
                os.fsync(parent)
                os.fsync(directory)
            raise


def finish(root: Path, marker: str, payload: bytes) -> None:
    """Persist the approved root and marker identities before claiming their names."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", root.name) or Path(marker).name != marker:
        raise ValueError("Invalid retirement identity")
    storage = root.parent / NAMESPACE
    with ExitStack() as stack:
        parent = stack.enter_context(_directory(root.parent))
        fcntl.flock(parent, fcntl.LOCK_EX)
        with suppress(FileExistsError):
            os.mkdir(NAMESPACE, mode=0o700, dir_fd=parent)
        os.fsync(parent)
        directory = stack.enter_context(_directory(storage))
        fcntl.flock(directory, fcntl.LOCK_EX)
        receipt = root.name + ".json"
        if not pending(root):
            with _directory(root) as active:
                info = os.fstat(active)
                data, entry = _read(active, marker, _MAX)
                if set(os.listdir(active)) != {marker} or data != payload:
                    raise ValueError("Directory contents changed before retirement")
                record = Checkpoint(
                    target=root.name,
                    directory=(info.st_dev, info.st_ino),
                    marker=marker,
                    entry=entry,
                    payload=data.decode(),
                )
            _publish(directory, receipt, record.model_dump_json().encode(), directory)
        name = _receipt(root)
        data, approved = _read(directory, name, 2 * _MAX + 4096)
        record = _parse(root, marker, data)
        if record.payload.encode() != payload:
            raise ValueError("Retirement approval changed")
        _remove(root, parent, directory, record)
        final = root.name + ".final"
        if name != final:
            rename_new(directory, name, directory, final)
            os.fsync(directory)
        try:
            _, claimed = _read(directory, final, 2 * _MAX + 4096)
            if claimed != approved:
                raise ValueError("A changed retirement receipt was preserved")
        except (OSError, ValueError):
            if name != final:
                with suppress(FileExistsError, FileNotFoundError):
                    rename_new(directory, final, directory, name)
            raise
        os.unlink(final, dir_fd=directory)
        os.fsync(directory)
