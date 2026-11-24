"""Owned HOME paths, bounded reads and atomic stdlib-only storage for delivery."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path


def state_root() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "setup-system"


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


def atomic_write(
    path: Path, content: bytes, mode: int = 0o600, *, preserve_existing_mode: bool = True
) -> str:
    target = inside_home(path)
    if target.exists():
        if not target.is_file():
            raise ValueError(f"Managed target is not a regular file: {target}")
        if target.read_bytes() == content and (
            preserve_existing_mode or stat.S_IMODE(target.stat().st_mode) == mode
        ):
            return "unchanged"
        if preserve_existing_mode:
            mode = stat.S_IMODE(target.stat().st_mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        candidate.chmod(mode)
        os.replace(candidate, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
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
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
        raise ValueError("Settings must be an owned, single-link regular file")
    if info.st_mode & 0o022:
        raise ValueError("Settings cannot be writable by other users")


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


def read_object(path: Path) -> dict[str, object]:
    safe_file(path, os.getuid())
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("File receipt exceeds the size limit")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("Invalid file receipt")
    return data
