"""Private TOML intentions with explicit, comment-preserving migration and conflict checks."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from platformdirs import user_config_path
from pydantic import ConfigDict, JsonValue, TypeAdapter

from setup_system.configuration.models import ApplicationOptions
from setup_system.configuration.sources import Profile, _application_options, _stage_options
from setup_system.domain.intentions import BaseOptions, Section, WorkspaceOptions
from setup_system.infrastructure.user_storage import FileSnapshot, publish_revision

Options = BaseOptions | WorkspaceOptions
_VALUES = TypeAdapter(dict[str, JsonValue], config=ConfigDict(hide_input_in_errors=True))
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


def _read(fd: int | None, name: str) -> FileSnapshot | None:
    if fd is None:
        return None
    try:
        return FileSnapshot.read(fd, name, limit=_LIMIT)
    except FileNotFoundError:
        return None
    except ValueError as error:
        raise ValueError(f"Unsafe settings file: {name}") from error


def _merge(table: MutableMapping[str, Any], desired: dict[str, Any]) -> None:
    for key in tuple(table):
        if key not in desired:
            del table[key]
    for key, value in desired.items():
        if isinstance(value, dict) and isinstance(table.get(key), MutableMapping):
            _merge(table[key], value)
        elif table.get(key) != value:
            table[key] = value


def _decode(section: Section, raw: bytes | None, legacy: bool) -> dict[str, JsonValue]:
    values = (
        _VALUES.validate_python(tomlkit.parse(raw.decode()).unwrap(), strict=True)
        if raw is not None
        else {}
    )
    version = values.pop("schema_version", 1)
    if type(version) is not int or version not in (
        (1, 2) if legacy and section == "workspace" else (1,)
    ):
        raise ValueError("Unknown settings schema; review the original file")
    if section == "base":
        return values
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
    return values


class Settings:
    def __init__(self, home: Path | None = None, config: Path | None = None) -> None:
        self.home = Path.home() if home is None else home
        if config is None and home is not None and not os.environ.get("XDG_CONFIG_HOME"):
            config = home / ".config"
        config_home = config if config is not None else user_config_path()
        self.directory = config_home / "setup-system"
        self._writer = asyncio.Lock()

    @staticmethod
    def application() -> ApplicationOptions:
        """Load the normal-user runtime preferences at explicit application startup."""
        return _application_options()

    async def read(
        self,
        section: Section,
        *,
        profile: Profile | None = None,
        confirmed: dict[str, JsonValue] | None = None,
    ) -> Loaded:
        """Read one owner-checked snapshot without blocking the terminal."""
        return await asyncio.to_thread(self.load, section, profile=profile, confirmed=confirmed)

    async def publish(self, section: Section, options: Options, expected: str) -> Loaded:
        """Keep ownership of the entire filesystem transaction until the worker returns."""
        async with self._writer:
            pending = asyncio.create_task(asyncio.to_thread(self.save, section, options, expected))
            cancelled = False
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancelled = True
            result = pending.result()
            if cancelled:
                raise asyncio.CancelledError
            return result

    def _source(self, fd: int | None, section: Section) -> tuple[str, FileSnapshot | None]:
        name = f"{section}.toml"
        raw = _read(fd, name)
        if raw is None:
            old = _LEGACY[section]
            old_bytes = _read(fd, old)
            if old_bytes is not None:
                return old, old_bytes
        return name, raw

    @staticmethod
    def _revision(name: str, snapshot: FileSnapshot | None) -> str:
        if snapshot is None:
            return name + ":absent"
        metadata = (
            snapshot.device,
            snapshot.inode,
            snapshot.group,
            snapshot.mode,
            snapshot.modified_ns,
        )
        return name + ":" + hashlib.sha256(repr(metadata).encode() + snapshot.data).hexdigest()

    def load(
        self,
        section: Section,
        *,
        profile: Profile | None = None,
        confirmed: dict[str, JsonValue] | None = None,
    ) -> Loaded:
        if section not in _LEGACY:
            raise ValueError("Unknown settings section")
        with _directory(self.home, self.directory, False) as fd:
            name, snapshot = self._source(fd, section)
            raw = snapshot.data if snapshot else None
            return Loaded(
                _stage_options(
                    section, _decode(section, raw, name == _LEGACY[section]), confirmed, profile
                ),
                self._revision(name, snapshot),
                name,
            )

    def save(self, section: Section, options: Options, expected: str) -> Loaded:
        if section not in _LEGACY:
            raise ValueError("Unknown settings section")
        target = f"{section}.toml"
        model = BaseOptions if section == "base" else WorkspaceOptions
        validated = model.model_validate(options)
        with _directory(self.home, self.directory, True) as fd:
            assert fd is not None
            fcntl.flock(fd, fcntl.LOCK_EX)
            name, snapshot = self._source(fd, section)
            raw = snapshot.data if snapshot else None
            if self._revision(name, snapshot) != expected:
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
            retained = f"{target}.bak-{uuid.uuid4().hex}" if name == target and snapshot else None
            published = publish_revision(fd, name, snapshot, fd, target, output, retained)
        return Loaded(validated, self._revision(target, published), target)
