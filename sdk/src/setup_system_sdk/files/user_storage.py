"""Check owned HOME paths and publish bounded, revision-checked user data."""

from __future__ import annotations

import os
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_state_path
from pydantic import JsonValue, TypeAdapter

from setup_system_sdk.files.atomic_rename import exchange, rename_new


def state_root() -> Path:
    return user_state_path("setup-system") / "v10" / "files"


def inside_home(path: Path) -> Path:
    home = Path.home().resolve()
    candidate = path.expanduser().absolute()
    if ".." in candidate.parts:
        raise ValueError("Managed path must stay inside HOME")
    try:
        candidate.relative_to(home)
    except ValueError as exc:
        raise ValueError(f"Managed path must be inside the current home: {candidate}") from exc
    for parent in (candidate, *candidate.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f"Managed path traverses a symlink: {parent}")
    return candidate


@contextmanager
def staged_file(parent: int, basename: str, content: bytes, mode: int) -> Iterator[str]:
    """Hold a synced private inode until the caller publishes it in the pinned directory."""
    pinned = Path("/proc/self/fd") / str(parent)
    descriptor, name = tempfile.mkstemp(prefix=f".{basename}.", dir=pinned)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        yield candidate.name
    finally:
        candidate.unlink(missing_ok=True)


def atomic_write(
    path: Path, content: bytes, mode: int = 0o600, *, preserve_existing_mode: bool = True
) -> str:
    target = inside_home(path)
    with private_directory(target.parent) as parent:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            previous = os.open(target.name, flags, dir_fd=parent)
        except FileNotFoundError:
            previous = None
        if previous is not None:
            try:
                info = os.fstat(previous)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"Managed target is not a regular file: {target}")
                previous_mode = stat.S_IMODE(info.st_mode)
                with open(previous, "rb", closefd=False) as original:
                    if original.read(len(content) + 1) == content and (
                        preserve_existing_mode or previous_mode == mode
                    ):
                        return "unchanged"
                if preserve_existing_mode:
                    mode = previous_mode
            finally:
                os.close(previous)
        with staged_file(parent, target.name, content, mode) as candidate:
            os.replace(candidate, target.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
    return "changed"


def safe_file(path: Path, uid: int) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Settings path must be absolute")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Settings paths cannot traverse symlinks")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    _check_file(info, uid)


def _check_file(info: os.stat_result, uid: int) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
        raise ValueError("Settings must be an owned, single-link regular file")
    if info.st_mode & 0o022:
        raise ValueError("Settings cannot be writable by other users")


def _check_directory(info: os.stat_result, path: Path) -> None:
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError(f"Unsafe owner or permissions on directory: {path}")


def checked_directory(path: Path) -> Path:
    home = Path.home().resolve()
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(home):
        raise ValueError(f"Managed path must stay inside HOME: {path}")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"Managed path traverses a symlink: {parent}")
        if parent.exists():
            _check_directory(parent.stat(), parent)
        if parent == home:
            break
    return path


@contextmanager
def private_directory(path: Path, *, create: bool = True) -> Iterator[int]:
    """Pin an owned directory, optionally creating missing components below HOME."""
    checked_directory(path)
    home = Path.home().resolve()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = Path("/")
    descriptor = os.open(current, flags)
    try:
        for part in path.parts[1:]:
            current /= part
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create or current == home or not current.is_relative_to(home):
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    # Another writer may have just created it. Validate its descriptor below.
                    pass
                else:
                    os.fsync(descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            if current.is_relative_to(home):
                _check_directory(os.fstat(descriptor), current)
        yield descriptor
    finally:
        os.close(descriptor)


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


@dataclass(frozen=True)
class FileSnapshot:
    data: bytes
    device: int
    inode: int
    mode: int
    group: int
    modified_ns: int

    def json(self) -> dict[str, JsonValue]:
        return TypeAdapter(dict[str, JsonValue]).validate_json(self.data, strict=True)

    @classmethod
    def read(cls, parent: int, name: str, *, limit: int = 4 * 1024 * 1024) -> FileSnapshot:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open(name, flags, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(descriptor)
            _check_file(info, os.getuid())
            if info.st_size > limit:
                raise ValueError("Managed file exceeds the size limit")
            data = stream.read(limit + 1)
            current = os.fstat(descriptor)
        if len(data) > limit or info.st_ctime_ns != current.st_ctime_ns:
            raise ValueError("Managed file changed during observation")
        return cls(data, info.st_dev, info.st_ino, info.st_mode, info.st_gid, info.st_mtime_ns)

    def matches(self, parent: int, name: str) -> bool:
        try:
            return self == self.read(parent, name, limit=len(self.data))
        except (ValueError, OSError):
            return False


def capture_receipt(path: Path) -> FileSnapshot:
    with private_directory(path.parent, create=False) as parent:
        return FileSnapshot.read(parent, path.name)


def _withdraw(parent: int, name: str, published: FileSnapshot, displaced: str | None) -> None:
    """Reclaim the candidate by name; keep any racing editor's inode."""
    claimed = f"{name}.conflict-{uuid.uuid4().hex}"
    try:
        rename_new(parent, name, parent, claimed)
    except FileNotFoundError:
        pass
    else:
        if not published.matches(parent, claimed):
            with suppress(FileExistsError):
                rename_new(parent, claimed, parent, name)
            os.fsync(parent)
            return
    if displaced is not None:
        with suppress(FileExistsError):
            rename_new(parent, displaced, parent, name)
    os.fsync(parent)


def publish_revision(
    origin: int,
    source: str,
    observed: FileSnapshot | None,
    parent: int,
    target: str,
    content: bytes,
    retained: str | None,
) -> FileSnapshot:
    """Publish a new name exclusively or exchange and verify the actual displaced inode."""
    with staged_file(parent, target, content, 0o600) as candidate:
        prepared = FileSnapshot.read(parent, candidate, limit=len(content))
        if retained is not None:
            assert observed is not None
            rename_new(parent, candidate, parent, retained)
            exchange(parent, retained, target)
            origin, source = parent, retained
        else:
            if observed is not None and not observed.matches(origin, source):
                raise ValueError("Concurrent edit before migration")
            rename_new(parent, candidate, parent, target)
        os.fsync(parent)
        if (observed is not None and not observed.matches(origin, source)) or not prepared.matches(
            parent, target
        ):
            _withdraw(parent, target, prepared, retained)
            raise ValueError(f"Concurrent edit; inspect {target} and its retained files")
        return prepared
