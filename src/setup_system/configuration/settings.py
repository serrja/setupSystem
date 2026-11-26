"""User-process settings and the location of the v10 database."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from platformdirs import user_state_path
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from setup_system_sdk.schemas.common import ActionId, Identifier
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ENV_PREFIX = "SETUP_SYSTEM_"


def default_database_url(state_home: Path) -> SecretStr:
    base = state_home / "setup-system"
    path = base / "v10" / "state.sqlite3"
    if not path.exists() and (base / "resources.sqlite3").exists():
        path = base / "resources.sqlite3"
    return SecretStr(URL.create("sqlite+aiosqlite", database=str(path)).render_as_string())


class Settings(BaseSettings):
    """Load host preferences from packaged TOML and process environment fields."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        frozen=True,
        hide_input_in_errors=True,
        extra="forbid",
        allow_inf_nan=False,
    )
    profile: Identifier = "default"
    catalogs: tuple[Path, ...] = ()
    providers: dict[ActionId, Identifier] = Field(default_factory=dict)
    suggestion_debounce_seconds: float = Field(ge=0, le=1)
    suggestion_cache_directories: int = Field(ge=1, le=128)
    suggestion_scan_entries: int = Field(ge=1, le=8192)
    suggestion_results: int = Field(ge=1, le=512)
    prompt_timeout_seconds: float = Field(ge=0.01, le=300)
    idle_timeout_seconds: float = Field(ge=0.01, le=3600)
    terminal_timeout_seconds: float = Field(ge=1, le=3600)
    termination_grace_seconds: float = Field(ge=0.01, le=30)
    read_only_timeout_seconds: float = Field(ge=0.01, le=86400)
    database_url: SecretStr = Field(
        default_factory=lambda: default_database_url(user_state_path()), repr=False
    )
    database_busy_timeout_ms: int = Field(default=5000, ge=100, le=10000)

    def __init__(self) -> None:
        super().__init__()

    @field_validator("database_url")
    @classmethod
    def local_database(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except (ArgumentError, ValueError):
            raise ValueError("Invalid local database URL") from None
        path = Path(url.database or "")
        if (
            url.drivername != "sqlite+aiosqlite"
            or url.host is not None
            or url.username is not None
            or url.password is not None
            or url.port is not None
            or url.query
            or not path.is_absolute()
            or ".." in path.parts
        ):
            raise ValueError("Select an absolute local SQLite database using sqlite+aiosqlite")
        return value

    @classmethod
    def environment_names(cls) -> frozenset[str]:
        return frozenset(ENV_PREFIX + name.upper() for name in cls.model_fields)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(
                settings_cls, Path(str(files("setup_system.configuration") / "application.toml"))
            ),
        )
