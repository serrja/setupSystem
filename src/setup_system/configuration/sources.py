"""Assemble explicit package, operation and owner-checked settings sources."""

from pathlib import Path
from typing import Any, Literal

from pydantic import JsonValue, StrictFloat, StrictInt, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from setup_system.configuration.models import ApplicationOptions
from setup_system.domain.intentions import BaseOptions, Section, WorkspaceOptions
from setup_system.layout import resource_root

APPLICATION_ENV_PREFIX = "SETUP_SYSTEM_"
Profile = Literal["example"]


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


class _StageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, env_file=None, secrets_dir=None
    )

    base: BaseOptions | None = None
    workspace: WorkspaceOptions | None = None

    def __init__(self, layers: list[dict[str, dict[str, JsonValue]]]) -> None:
        super().__init__(layers=layers)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return tuple(InitSettingsSource(settings_cls, layer) for layer in init_settings()["layers"])


def _application_options() -> ApplicationOptions:
    return ApplicationOptions.model_validate(_ApplicationSettings())


def _stage_options(
    section: Section,
    saved: dict[str, JsonValue],
    confirmed: dict[str, JsonValue] | None,
    profile: Profile | None,
) -> BaseOptions | WorkspaceOptions:
    model = BaseOptions if section == "base" else WorkspaceOptions
    model.model_validate(saved)
    layers: list[dict[str, dict[str, JsonValue]]] = [
        {section: {} if confirmed is None else confirmed},
        {section: saved},
    ]
    if profile is not None:
        if profile != "example":
            raise ValueError("Unknown packaged settings profile")
        path = _package_file(Path(section) / "example.toml")
        values = TomlConfigSettingsSource(_StageSettings, path)()
        version = values.pop("schema_version", None)
        if type(version) is not int or version != 1:
            raise ValueError("Unknown packaged settings schema")
        model.model_validate(values)
        layers.append({section: values})
    resolved = _StageSettings(layers=layers)
    options = resolved.base if section == "base" else resolved.workspace
    assert options is not None
    return options
