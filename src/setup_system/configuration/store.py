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
from typing import TYPE_CHECKING, Any

import tomlkit
from platformdirs import user_config_path
from pydantic import TypeAdapter

from setup_system.configuration.models import ApplicationOptions
from setup_system.configuration.sources import _application_options
from setup_system.infrastructure.atomic_rename import rename_new
from setup_system.infrastructure.user_storage import FileSnapshot, publish_revision
from setup_system_sdk import Identifier, Outcome, Plan, Resource, Result, Values

if TYPE_CHECKING:
    from setup_system.resource_ledger import ResourceLedger

_NAMES = TypeAdapter(Identifier)
_VALUES = TypeAdapter(Values)
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


def _merge(table: MutableMapping[str, Any], desired: dict[str, Any]) -> None:
    for key in tuple(table):
        if key not in desired:
            del table[key]
    for key, value in desired.items():
        if isinstance(value, dict) and isinstance(table.get(key), MutableMapping):
            _merge(table[key], value)
        elif table.get(key) != value:
            table[key] = value


@dataclass(frozen=True)
class SavedValues:
    values: Values
    revision: str
    version: int


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

    async def read(self, plugin: str, profile: str = "default") -> SavedValues:
        return await asyncio.to_thread(load_values, self, plugin, profile)

    def for_version(self, schema: int) -> Settings:
        """Keep older configurations readable by the previously active release."""
        if schema < 1:
            raise ValueError("Invalid profile schema")
        if schema == 1:
            return self
        result = Settings(self.home, self.directory.parent)
        result.directory = self.directory / "schemas" / str(schema)
        return result

    async def publish(
        self, plugin: str, profile: str, values: Values, version: int, revision: str
    ) -> SavedValues:
        async with self._writer:
            pending = asyncio.create_task(
                asyncio.to_thread(save_values, self, plugin, profile, values, version, revision)
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


def load_values(store: Settings, plugin: str, profile: str) -> SavedValues:
    name = _NAMES.validate_python(profile) + ".toml"
    directory = store.directory / "plugins" / _NAMES.validate_python(plugin)
    with _directory(store.home, directory, False) as fd:
        snapshot = _read(fd, name)
        version = 1
        values: Values = {}
        if snapshot:
            data = tomlkit.parse(snapshot.data.decode()).unwrap()
            version = data["schema_version"]
            if type(version) is not int or version < 1:
                raise ValueError("Unknown settings version")
            values = _VALUES.validate_python(data["values"])
        return SavedValues(values, store._revision(name, snapshot), version)


def save_values(
    store: Settings, plugin: str, profile: str, values: Values, version: int, revision: str
) -> SavedValues:
    name = _NAMES.validate_python(profile) + ".toml"
    directory = store.directory / "plugins" / _NAMES.validate_python(plugin)
    with _directory(store.home, directory, True) as fd:
        assert fd is not None
        fcntl.flock(fd, fcntl.LOCK_EX)
        snapshot = _read(fd, name)
        if store._revision(name, snapshot) != revision:
            raise ValueError("Settings changed. Reload the profile before saving.")
        document = tomlkit.parse(snapshot.data.decode()) if snapshot else tomlkit.document()
        _merge(
            document,
            {
                "schema_version": version,
                "values": {k: v for k, v in values.items() if v is not None},
            },
        )
        data = tomlkit.dumps(document).encode()
        if len(data) > 65536:
            raise ValueError("Settings exceed the size limit")
        if snapshot and data == snapshot.data:
            return SavedValues(values, revision, version)
        backup = f"{name}.bak-{uuid.uuid4().hex}" if snapshot else None
        result = publish_revision(fd, name, snapshot, fd, name, data, backup)
        return SavedValues(values, store._revision(name, result), version)


def profiles(store: Settings, plugin: str) -> list[str]:
    directory = store.directory / "plugins" / _NAMES.validate_python(plugin)
    with _directory(store.home, directory, False) as fd:
        if fd is None:
            return ["default"]

        names = [Path(name).stem for name in os.listdir(fd) if name.endswith(".toml")]
        return sorted({"default", *names})


async def forget_profile(
    store: Settings, ledger: ResourceLedger, plugin: str, row: Resource
) -> None:
    directory = store.directory / "plugins" / _NAMES.validate_python(plugin)
    path = Path(row.identity)
    if path.parent != directory or path.suffix != ".toml" or not row.after:
        raise ValueError("Invalid profile resource")
    _NAMES.validate_python(path.stem)
    operation = uuid.uuid4()
    change = row.model_copy(update={"effect": "remove", "before": row.after, "after": None})
    await ledger.prepare(
        operation,
        plugin,
        "remove-profile",
        "1",
        Plan(summary="Remove owned profile", outcome=Outcome.READY, resources=[change]),
    )

    def remove() -> None:
        with _directory(store.home, directory, False) as fd:
            if fd is None:
                raise ValueError("Profile directory disappeared")
            fcntl.flock(fd, fcntl.LOCK_EX)
            source = _read(fd, path.name)
            if source is None or store._revision(path.name, source) != row.after:
                raise ValueError("Profile changed after cleanup confirmation")
            held = ".removing-" + operation.hex
            rename_new(fd, path.name, fd, held)
            if not source.matches(fd, held):
                rename_new(fd, held, fd, path.name)
                raise ValueError("A concurrent edit was retained")
            os.unlink(held, dir_fd=fd)
            os.fsync(fd)
            if _read(fd, path.name) is not None:
                raise ValueError("Another profile appeared during cleanup")

    try:
        await asyncio.to_thread(remove)
    except (ValueError, OSError):
        await ledger.finish(
            operation, Result(outcome=Outcome.PARTIAL, message="Profile removal needs inspection")
        )
        raise
    await ledger.finish(
        operation,
        Result(outcome=Outcome.COMPLETE, message="Profile removal verified", resources=[change]),
    )


def reconcile_removed_profile(path: Path, operation: uuid.UUID, revision: str | None) -> bool:
    """Finish unlinking the exact file already claimed by a confirmed profile removal."""
    with _directory(Path.home(), path.parent, False) as fd:
        if fd is None:
            return True
        fcntl.flock(fd, fcntl.LOCK_EX)
        held = ".removing-" + operation.hex
        staged = _read(fd, held)
        if _read(fd, path.name) is not None:
            if staged:
                raise ValueError("Both the edited profile and its recovery copy remain")
            return False
        if staged:
            if Settings._revision(path.name, staged) != revision or not staged.matches(fd, held):
                raise ValueError("The staged profile changed during removal")
            os.unlink(held, dir_fd=fd)
            os.fsync(fd)
        return _read(fd, path.name) is None and _read(fd, held) is None
