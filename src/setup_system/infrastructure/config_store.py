"""Typed, comment-preserving TOML with atomic writes and optimistic conflict checks."""

from __future__ import annotations

import hashlib
import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import tomlkit
from platformdirs import user_cache_path, user_config_path, user_state_path
from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from setup_system.domain.catalog import ComponentCatalog
from setup_system.domain.settings import V1_APP_KEYS, SystemSettings, UserSettings
from setup_system.infrastructure.catalog_repository import load_components
from setup_system.infrastructure.user_storage import safe_file


class _EnvironmentDefaults(EnvSettingsSource):
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if field_name in self.current_state:
            return None, field_name, False
        return super().get_field_value(field, field_name)

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        if field.annotation is bool and isinstance(value, str):
            return TypeAdapter(bool).validate_python(value)
        return super().prepare_field_value(field_name, field, value, value_is_complex)


_SETTINGS_CONFIG = SettingsConfigDict(extra="forbid", frozen=True, env_prefix="SETUP_SYSTEM_")


class _FileSettings(BaseSettings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, _EnvironmentDefaults(settings_cls)


class _UserSettingsBoundary(UserSettings, _FileSettings):
    model_config = _SETTINGS_CONFIG


class _SystemSettingsBoundary(SystemSettings, _FileSettings):
    model_config = _SETTINGS_CONFIG


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


class ConfigStore[T: BaseModel]:
    def __init__(
        self,
        path: Path,
        model: type[T],
        *,
        uid: int | None = None,
        catalog: ComponentCatalog | None = None,
    ) -> None:
        self.catalog = catalog
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
        if any(key.startswith("_") for key in values):
            raise ValueError("Settings cannot select additional configuration sources")
        if self.model is UserSettings:
            if self.catalog is None:
                self.catalog = load_components()
            validated = UserSettings.model_validate(_UserSettingsBoundary(**values).model_dump())
            return cast(T, self.catalog.validate_settings(validated))
        if self.model is SystemSettings:
            return cast(
                T, SystemSettings.model_validate(_SystemSettingsBoundary(**values).model_dump())
            )
        return self.model.model_validate(values)

    def prepare(self, settings: T, expected: str | None) -> bytes:
        _, actual = self.read()
        settings = self.model.model_validate(settings)
        if isinstance(settings, UserSettings) and self.catalog is not None:
            self.catalog.validate_settings(settings)
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
        from setup_system.infrastructure.managed_files import apply_files

        output = self.prepare(settings, expected)
        apply_files({self.path: output}, modes={self.path: 0o600}, expected={self.path: expected})
        return hashlib.sha256(output).hexdigest()


def save_intentions(
    user: UserSettings,
    system: SystemSettings,
    user_revision: str | None,
    draft_revision: str | None,
    *,
    user_store: ConfigStore[UserSettings],
    draft_store: ConfigStore[SystemSettings],
) -> tuple[str, str]:
    from setup_system.infrastructure.managed_files import apply_files

    path, draft = user_store.path, draft_store.path
    user_bytes = user_store.prepare(user, user_revision)
    draft_bytes = draft_store.prepare(system, draft_revision)
    apply_files(
        {path: user_bytes, draft: draft_bytes},
        modes={path: 0o600, draft: 0o600},
        expected={path: user_revision, draft: draft_revision},
    )
    return hashlib.sha256(user_bytes).hexdigest(), hashlib.sha256(draft_bytes).hexdigest()
