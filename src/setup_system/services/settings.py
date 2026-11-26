"""Persist one revision-checked TOML document per extension."""

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

import tomlkit
from platformdirs import user_config_path
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from setup_system.execution.user_storage import FileSnapshot, publish_revision
from setup_system_sdk.schemas.common import Identifier, Values

_NAMES = TypeAdapter(Identifier)
_LIMIT = 65536
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


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


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: int = Field(ge=1)
    values: Values


def _toml_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _toml_value(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        if None in value:
            raise ValueError("TOML lists cannot contain null items")
        return [_toml_value(item) for item in value]
    return value


def _merge(table: MutableMapping[str, JsonValue], desired: Values) -> None:
    for key in tuple(table):
        if key not in desired:
            del table[key]
    for key, value in desired.items():
        current = table.get(key)
        if isinstance(value, dict) and isinstance(current, MutableMapping):
            _merge(current, value)
        elif table.get(key) != value:
            table[key] = value


@dataclass(frozen=True)
class SavedValues:
    values: Values
    revision: str
    version: int


class SettingsStore:
    def __init__(self, home: Path | None = None, config: Path | None = None) -> None:
        self.home = Path.home() if home is None else home
        if config is None and home is not None and not os.environ.get("XDG_CONFIG_HOME"):
            config = home / ".config"
        config_home = config if config is not None else user_config_path()
        self.directory = config_home / "setup-system" / "v10" / "plugins"
        self._writer = asyncio.Lock()

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

    async def read(self, plugin: str) -> SavedValues:
        return await asyncio.to_thread(load_values, self, plugin)

    async def write(self, plugin: str, values: Values, version: int, revision: str) -> SavedValues:
        async with self._writer:
            pending = asyncio.create_task(
                asyncio.to_thread(save_values, self, plugin, values, version, revision)
            )
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        continue
                pending.result()
                raise


def load_values(store: SettingsStore, plugin: str) -> SavedValues:
    name = _NAMES.validate_python(plugin) + ".toml"
    directory = store.directory
    with _directory(store.home, directory, False) as fd:
        snapshot = _read(fd, name)
        version = 1
        values: Values = {}
        if snapshot:
            document = _Document.model_validate(tomlkit.parse(snapshot.data.decode()).unwrap())
            version, values = document.schema_version, document.values
        return SavedValues(values, store._revision(name, snapshot), version)


def save_values(
    store: SettingsStore, plugin: str, values: Values, version: int, revision: str
) -> SavedValues:
    name = _NAMES.validate_python(plugin) + ".toml"
    directory = store.directory
    with _directory(store.home, directory, True) as fd:
        assert fd is not None
        fcntl.flock(fd, fcntl.LOCK_EX)
        snapshot = _read(fd, name)
        if store._revision(name, snapshot) != revision:
            raise ValueError("Settings changed. Reload this extension before saving.")
        document = tomlkit.parse(snapshot.data.decode()) if snapshot else tomlkit.document()
        settings = _Document(
            schema_version=version,
            values={key: _toml_value(item) for key, item in values.items() if item is not None},
        )
        _merge(document, settings.model_dump())
        data = tomlkit.dumps(document).encode()
        if len(data) > _LIMIT:
            raise ValueError("Settings exceed the size limit")
        if snapshot and data == snapshot.data:
            return SavedValues(settings.values, revision, version)
        backup = f"{name}.bak-{uuid.uuid4().hex}" if snapshot else None
        result = publish_revision(fd, name, snapshot, fd, name, data, backup)
        return SavedValues(settings.values, store._revision(name, result), version)
