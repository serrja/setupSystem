"""Load the packaged application settings and explicit shell environment overrides."""

from pathlib import Path
from typing import Any

from pydantic import StrictFloat, StrictInt, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from setup_system.configuration.models import ApplicationOptions
from setup_system.layout import resource_root

APPLICATION_ENV_PREFIX = "SETUP_SYSTEM_"


def _package_file(relative: Path) -> Path:
    path = resource_root() / "config" / relative
    if not path.is_file() or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError(f"Missing or unsafe package settings: {relative}")
    return path


_NUMBER = TypeAdapter(StrictInt | StrictFloat)


class _ApplicationEnvironment(EnvSettingsSource):
    """Accept numeric preferences only under their canonical uppercase names."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        raw = self.env_vars.get(APPLICATION_ENV_PREFIX + field_name.upper())
        if raw is None:
            return None, field_name, False
        try:
            return _NUMBER.validate_json(raw), field_name, False
        except ValueError:
            raise ValueError("Expected a finite numeric preference") from None


class _ApplicationSettings(BaseSettings, ApplicationOptions):
    model_config = SettingsConfigDict(
        case_sensitive=True, env_prefix=APPLICATION_ENV_PREFIX, env_file=None, secrets_dir=None
    )

    def __init__(self) -> None:
        super().__init__()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        packaged = TomlConfigSettingsSource(settings_cls, _package_file(Path("application.toml")))
        # A higher-priority environment value cannot repair an incomplete installation.
        ApplicationOptions.model_validate(packaged(), strict=True)
        return _ApplicationEnvironment(settings_cls), packaged


def _application_options() -> ApplicationOptions:
    return ApplicationOptions.model_validate(_ApplicationSettings())
