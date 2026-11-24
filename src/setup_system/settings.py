"""Private TOML intentions with explicit, comment-preserving migration and conflict checks."""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import os
import stat
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from platformdirs import user_config_path

from setup_system.infrastructure.atomic_rename import rename_new
from setup_system.models import BaseOptions, Section, WorkspaceOptions

Options = BaseOptions | WorkspaceOptions
_LEGACY = {"base": "system-intentions.toml", "workspace": "settings.toml"}
_V1_APPS = {"neovim", "firefox", "codex", "chatgpt", "happ", "developer_tools"}
_LIMIT = 65536
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True)
class Loaded:
    options: Options
    revision: str
    source: str


@contextmanager
def _directory(home: Path, directory: Path, create: bool) -> Iterator[int | None]:
    if (
        not home.is_absolute()
        or not directory.is_relative_to(home)
        or ".." in directory.parts
        or directory == home
    ):
        raise ValueError("Settings directory must be inside the user's absolute home")
    fd = os.open("/", _DIR)
    try:
        for i, part in enumerate(directory.parts[1:], 1):
            try:
                child = os.open(part, _DIR, dir_fd=fd)
            except FileNotFoundError:
                if i < len(home.parts) or not create:
                    if not create:
                        yield None
                        return
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, _DIR, dir_fd=fd)
                os.fsync(fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if i >= len(home.parts) - 1 and (info.st_uid != os.getuid() or info.st_mode & 0o022):
                raise ValueError("Settings directory must be owner-controlled")
        yield fd
    finally:
        os.close(fd)


def _read(fd: int | None, name: str) -> bytes | None:
    if fd is None:
        return None
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(file_fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError(f"Unsafe settings file: {name}")
        data = stream.read(_LIMIT + 1)
        if len(data) > _LIMIT:
            raise ValueError("Settings exceed the size limit")
        return data


def _exchange(fd: int, backup: str, target: str) -> None:
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    if rename(fd, os.fsencode(backup), fd, os.fsencode(target), 2):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _merge(table: MutableMapping[str, Any], desired: dict[str, Any]) -> None:
    for key in tuple(table):
        if key not in desired:
            del table[key]
    for key, value in desired.items():
        if isinstance(value, dict) and isinstance(table.get(key), MutableMapping):
            _merge(table[key], value)
        elif table.get(key) != value:
            table[key] = value


def _decode(section: Section, raw: bytes | None, legacy: bool) -> Options:
    values: dict[str, Any] = tomlkit.parse(raw.decode()).unwrap() if raw is not None else {}
    version = values.pop("schema_version", 1)
    if type(version) is not int or version not in (
        (1, 2) if legacy and section == "workspace" else (1,)
    ):
        raise ValueError("Unknown settings schema; review the original file")
    if section == "base":
        return BaseOptions.model_validate(values)
    if legacy:
        if version == 1:
            allowed = _V1_APPS | {
                "theme",
                "language_profiles",
                "optional_apps",
                "cleanup",
                "cleanup_mode",
            }
            if values.keys() - allowed or any(
                type(values[k]) is not bool for k in values.keys() & _V1_APPS
            ):
                raise ValueError("Invalid version 1 application selections")
            values["apps"] = [key for key in sorted(_V1_APPS) if values.pop(key, False)]
        apps = values.get("apps", [])
        optional = values.pop("optional_apps", [])
        if not isinstance(apps, list) or not isinstance(optional, list) or "languages" in values:
            raise ValueError("Invalid legacy workspace selections")
        values["apps"] = apps + optional
        values["languages"] = values.pop("language_profiles", [])
        theme = values.get("theme", {})
        if (
            not isinstance(theme, dict)
            or theme.pop("name", "catppuccin_mocha_green") != "catppuccin_mocha_green"
        ):
            raise ValueError("Unknown legacy theme")
    return WorkspaceOptions.model_validate(values)


class Settings:
    def __init__(self, home: Path | None = None, config: Path | None = None) -> None:
        self.home = Path.home() if home is None else home
        if config is None and home is not None and not os.environ.get("XDG_CONFIG_HOME"):
            config = home / ".config"
        config_home = config if config is not None else user_config_path()
        self.directory = config_home / "setup-system"

    def _source(self, fd: int | None, section: Section) -> tuple[str, bytes | None]:
        name = f"{section}.toml"
        raw = _read(fd, name)
        if raw is None:
            old = _LEGACY[section]
            old_bytes = _read(fd, old)
            if old_bytes is not None:
                return old, old_bytes
        return name, raw

    @staticmethod
    def _revision(name: str, raw: bytes | None) -> str:
        return name + ":" + (hashlib.sha256(raw).hexdigest() if raw is not None else "absent")

    def load(self, section: Section) -> Loaded:
        with _directory(self.home, self.directory, False) as fd:
            name, raw = self._source(fd, section)
            return Loaded(
                _decode(section, raw, name == _LEGACY[section]), self._revision(name, raw), name
            )

    def save(self, section: Section, options: Options, expected: str) -> Loaded:
        target = f"{section}.toml"
        model = BaseOptions if section == "base" else WorkspaceOptions
        validated = model.model_validate(options)
        with _directory(self.home, self.directory, True) as fd:
            assert fd is not None
            fcntl.flock(fd, fcntl.LOCK_EX)
            name, raw = self._source(fd, section)
            if self._revision(name, raw) != expected:
                raise ValueError("Settings changed; reload before saving")
            document = tomlkit.parse(raw.decode()) if raw is not None else tomlkit.document()
            _merge(
                document,
                {"schema_version": 1, **validated.model_dump(mode="json", exclude_none=True)},
            )
            output = tomlkit.dumps(document).encode()
            if len(output) > _LIMIT:
                raise ValueError("Settings exceed the size limit")
            if name == target and raw == output:
                return Loaded(validated, expected, target)
            backup = f"{target}.bak-{uuid.uuid4().hex}"
            new_fd = os.open(
                backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            keep = False
            try:
                with os.fdopen(new_fd, "wb") as stream:
                    stream.write(output)
                    stream.flush()
                    os.fsync(stream.fileno())
                if name != target or raw is None:
                    if name != target and _read(fd, name) != raw:
                        raise ValueError("Concurrent edit to the original settings; reload")
                    os.link(backup, target, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    if name != target and _read(fd, name) != raw:
                        # Claim the published name before rolling back. Retain both versions
                        # if an editor replaced it or wrote through an open descriptor.
                        conflict = backup + ".conflict"
                        try:
                            rename_new(fd, target, fd, conflict)
                        except FileNotFoundError:
                            pass
                        else:
                            candidate = os.stat(backup, dir_fd=fd, follow_symlinks=False)
                            claimed = os.stat(conflict, dir_fd=fd, follow_symlinks=False)
                            if (
                                not os.path.samestat(candidate, claimed)
                                or _read(fd, conflict) != output
                            ):
                                try:
                                    rename_new(fd, conflict, fd, target)
                                except FileExistsError:
                                    pass
                        os.fsync(fd)
                        raise ValueError(
                            "Concurrent edit during migration; inspect the original and backup"
                        )

                else:
                    # The displaced inode remains recoverable, including writes by an open editor.
                    _exchange(fd, backup, target)
                    keep = True
                    os.fsync(fd)
                    try:
                        matches = _read(fd, backup) == raw
                    except (ValueError, OSError):
                        matches = False
                    if not matches:
                        if _read(fd, target) == output:
                            _exchange(fd, backup, target)
                            os.fsync(fd)
                        raise ValueError(f"Concurrent settings edit; inspect {target} and {backup}")
                os.fsync(fd)
            finally:
                if not keep:
                    os.unlink(backup, dir_fd=fd)
                    os.fsync(fd)
        return Loaded(validated, self._revision(target, output), target)
