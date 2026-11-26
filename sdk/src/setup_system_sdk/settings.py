"""Resolve declared plugin settings and inputs."""

import os
from typing import ClassVar, Self

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from setup_system_sdk.schemas.common import Inputs as Inputs
from setup_system_sdk.schemas.common import Values


class PluginSettings(BaseSettings):
    """Resolve one current configuration from the sources accepted by the plugin."""

    model_config = SettingsConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, env_file=None, secrets_dir=None
    )
    environment_fields: ClassVar[dict[str, str]] = {}
    schema_version: ClassVar[int] = 1

    @classmethod
    def migrate(cls, values: Values, previous_version: int) -> Values:
        if previous_version != cls.schema_version:
            raise ValueError("This settings version requires a plugin migration")
        return values

    @classmethod
    def resolve(cls, *, saved: Values, overrides: Values) -> Self:
        environment = {
            field: os.environ[name]
            for name, field in cls.environment_fields.items()
            if name in os.environ
        }
        return cls.model_validate({**saved, **environment, **overrides})

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)
