"""Typed, comment-preserving TOML with atomic writes and optimistic conflict checks."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import tomlkit
from platformdirs import user_cache_path, user_config_path, user_state_path
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from setup_system.domain.settings import V1_APP_KEYS, SystemSettings, UserSettings


class _UserSettingsBoundary(UserSettings, BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", frozen=True, env_prefix="SETUP_SYSTEM_")


class _SystemSettingsBoundary(SystemSettings, BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", frozen=True, env_prefix="SETUP_SYSTEM_")


@dataclass(frozen=True)
class SettingsPaths:
    system: Path = Path("/etc/setup-system/system.toml")
    receipts: Path = Path("/var/lib/setup-system")

    @property
    def user(self) -> Path:
        return user_config_path("setup-system") / "settings.toml"

    @property
    def state(self) -> Path:
        return user_state_path("setup-system")

    @property
    def cache(self) -> Path:
        return user_cache_path("setup-system")


def safe_file(path: Path, uid: int) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Settings path must be absolute")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Settings paths cannot traverse symlinks")
    try:
        info = path.stat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
        raise ValueError("Settings must be an owned, single-link regular file")
    if info.st_mode & 0o022:
        raise ValueError("Settings cannot be writable by other users")


class ConfigStore[T: BaseModel]:
    def __init__(self, path: Path, model: type[T], *, uid: int | None = None) -> None:
        self.path, self.model = path, model
        self.uid = os.getuid() if uid is None else uid

    def read(self) -> tuple[T, str | None]:
        safe_file(self.path, self.uid)
        if not self.path.exists():
            return self._validate({}), None
        if self.path.stat().st_size > 65536:
            raise ValueError("Settings exceed the size limit")
        raw = self.path.read_bytes()
        document = tomlkit.parse(raw.decode())
        # Version zero had no explicit version; migration is in memory until an explicit save.
        if "schema_version" not in document:
            document["schema_version"] = 1
        return self._validate(document.unwrap()), hashlib.sha256(raw).hexdigest()

    def _validate(self, values: dict[str, Any]) -> T:
        if self.model is UserSettings:
            return cast(
                T, UserSettings.model_validate(_UserSettingsBoundary(**values).model_dump())
            )
        if self.model is SystemSettings:
            return cast(
                T, SystemSettings.model_validate(_SystemSettingsBoundary(**values).model_dump())
            )
        return self.model.model_validate(values)

    def prepare(self, settings: T, expected: str | None) -> bytes:
        _, actual = self.read()
        if actual != expected:
            raise ValueError("Settings changed since inspection; reload before saving")
        raw = self.path.read_bytes() if self.path.exists() else b""
        document = tomlkit.parse(raw.decode()) if raw else tomlkit.document()
        values = settings.model_dump(mode="json", exclude_none=True)

        def merge(target: MutableMapping[str, Any], desired: dict[str, Any]) -> None:
            for key, value in desired.items():
                existing = target.get(key)
                if isinstance(value, dict) and isinstance(existing, MutableMapping):
                    merge(existing, value)
                else:
                    target[key] = value

        if isinstance(settings, UserSettings) and document.get("schema_version", 1) == 1:
            for key in V1_APP_KEYS:
                document.pop(key, None)
        for key in type(settings).model_fields:
            if key not in values and key in document:
                del document[key]
        merge(document, values)
        return tomlkit.dumps(document).encode()

    def save(self, settings: T, expected: str | None) -> str:
        from setup_system.infrastructure.user_files import apply_files

        output = self.prepare(settings, expected)
        apply_files({self.path: output}, modes={self.path: 0o600}, expected={self.path: expected})
        return hashlib.sha256(output).hexdigest()


def save_intentions(
    user: UserSettings,
    system: SystemSettings,
    user_revision: str | None,
    draft_revision: str | None,
) -> tuple[str, str]:
    from setup_system.infrastructure.user_files import apply_files

    path = SettingsPaths().user
    draft = path.parent / "system-intentions.toml"
    user_bytes = ConfigStore(path, UserSettings).prepare(user, user_revision)
    draft_bytes = ConfigStore(draft, SystemSettings).prepare(system, draft_revision)
    apply_files(
        {path: user_bytes, draft: draft_bytes},
        modes={path: 0o600, draft: 0o600},
        expected={path: user_revision, draft: draft_revision},
    )
    return hashlib.sha256(user_bytes).hexdigest(), hashlib.sha256(draft_bytes).hexdigest()
