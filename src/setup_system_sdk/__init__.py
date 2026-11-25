"""Public contracts shared by the manager and independently installed backends."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

PLUGIN_GROUP = "setup_system.plugins"
UI_GROUP = "setup_system.plugin_ui"
SDK_VERSION = "1.1"
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
ActionId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")]
EntryName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*:[A-Za-z][A-Za-z0-9_]*$")]
Values = dict[str, JsonValue]


class LifecycleAction(StrEnum):
    CHECK = "check"
    INSTALL = "install"
    UPDATE = "update"
    CONFIGURE = "configure"
    UNINSTALL = "uninstall"


class Inputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class UIHints(Inputs):
    widget: str | None = None
    group: str | None = None
    order: int = 0
    choice_provider: Identifier | None = None
    allow_custom: bool = False
    readonly: bool = False
    secret: bool = False
    units: str | None = None
    visible_when: dict[Identifier, JsonValue] = Field(default_factory=dict)
    enabled_when: dict[Identifier, JsonValue] = Field(default_factory=dict)
    depends_on: tuple[Identifier, ...] = ()
    affects_privilege: bool = False
    requires_restart: bool = False
    storage: Literal["profile", "action", "keyring"] = "profile"


class EnvironmentValue(Inputs):
    """Subclasses narrow names, values and scopes for a specific native action."""

    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    value: str = Field(max_length=1024)
    scope: Literal["command"] = "command"

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if (
            self.name
            in {
                "SHELL",
                "PATH",
                "HOME",
                "IFS",
                "ENV",
                "BASH_ENV",
                "GNUMAKEFLAGS",
                "SHELLOPTS",
                "CDPATH",
            }
            or self.name.startswith(
                ("MAKE", "PYTHON", "LD_", "DYLD_", "ANSIBLE_", "BASH", "SETUP_SYSTEM_")
            )
            or "\x00" in self.value
        ):
            raise ValueError("Reserved executor environment")
        return self


class Layers(Inputs):
    profile: Values = Field(default_factory=dict)
    saved: Values = Field(default_factory=dict)
    environment: Values = Field(default_factory=dict)


_layers: ContextVar[Layers | None] = ContextVar("setup_system_layers", default=None)


class PluginSettings(BaseSettings):
    """Load only caller-selected sources and explicitly declared environment names."""

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
    def resolve(cls, *, profile: Values, saved: Values, overrides: Values) -> Self:
        allowed: Values = {
            field: os.environ[name]
            for name, field in cls.environment_fields.items()
            if name in os.environ
        }
        token = _layers.set(Layers(profile=profile, saved=saved, environment=allowed))
        try:
            return cls.model_validate(overrides)
        finally:
            _layers.reset(token)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        layers = _layers.get()
        if layers is None:
            return (init_settings,)
        return (
            init_settings,
            *(
                InitSettingsSource(settings_cls, values)
                for values in (layers.environment, layers.saved, layers.profile)
            ),
        )


class Outcome(StrEnum):
    NO_CHANGES = "no_changes"
    READY = "ready"
    CHANGED = "changed"
    COMPLETE = "complete"
    NEEDS_USER_ACTION = "needs_user_action"
    NEEDS_REBOOT = "needs_reboot"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class Resource(Inputs):
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    kind: Identifier
    identity: str = Field(min_length=1, max_length=4096)
    verified: bool = False
    remote_id: str | None = Field(default=None, max_length=512)
    ownership: Literal["created", "modified", "preexisting", "shared"]
    before: str | None = None
    after: str | None = None
    cleanup: bool = False
    effect: Literal["ensure", "remove"] = "ensure"
    before_permissions: int | None = Field(default=None, ge=0, le=0o7777)
    permissions: int | None = Field(default=None, ge=0, le=0o7777)
    consumers: tuple[Identifier, ...] = ()

    def description(self) -> str:
        """Show exact approved versions, remote objects and permissions in consent."""
        values = [f"{self.effect} {self.kind} · {self.identity} · {self.ownership}"]
        for label, value in (
            ("Before", self.before),
            ("After", self.after),
            ("Source SHA-256", self.source_sha256),
            ("Remote ID", self.remote_id),
        ):
            if value is not None:
                values.append(f"{label}: {value}")
        for label, mode in (
            ("Previous permissions", self.before_permissions),
            ("Permissions", self.permissions),
        ):
            if mode is not None:
                values.append(f"{label}: {mode:04o}")
        if self.consumers:
            values.append("Consumers: " + ", ".join(self.consumers))
        return " · ".join(
            "".join(c if c.isprintable() else repr(c)[1:-1] for c in v) for v in values
        )


_ownership: ContextVar[tuple[Resource, ...]] = ContextVar("owned_resources", default=())


def owned_resources() -> tuple[Resource, ...]:
    return _ownership.get()


@contextmanager
def ownership_context(resources: tuple[Resource, ...]) -> Iterator[None]:
    token = _ownership.set(resources)
    try:
        yield
    finally:
        _ownership.reset(token)


class Plan(Inputs):
    summary: str = Field(max_length=8192)
    resources: list[Resource] = Field(default_factory=list, max_length=4096)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    outcome: Outcome = Outcome.READY


class ActionEvent(Inputs):
    """A worker observation. The host binds its operation, action and plugin identity."""

    level: Literal["info", "warning", "error"] = "info"
    phase: Literal["planned", "prepared", "applied", "verified", "unresolved", "blocked"]
    message_code: Identifier
    outcome: Outcome | None = None
    details: dict[Literal["resource_count"], int] = Field(default_factory=dict)


class Result(Inputs):
    events: tuple[ActionEvent, ...] = Field(default=(), max_length=64)
    recovery_id: str | None = Field(default=None, pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
    outcome: Outcome
    message: str = Field(max_length=8192)
    resources: list[Resource] = Field(default_factory=list, max_length=4096)


class Action(Inputs):
    cleanup_phase: Literal["remote", "local", "program"] | None = None
    id: ActionId
    label: str
    page: Literal["actions", "accounts"] = "actions"
    target: Identifier | None = None
    permissions: list[str] = Field(default_factory=list)
    supports_rollback: bool = False
    read_only: bool = False
    description: str = ""


def _validate_ui(schema: Values) -> None:
    hints = schema.get("ui")
    if hints is not None:
        ui = UIHints.model_validate(hints)
        if ui.secret and ui.widget != "secret_reference":
            raise ValueError("Plugin secrets require a native secret reference")
    if schema.get("writeOnly") or schema.get("format") == "password":
        raise ValueError("Persistent models must use secret references")
    children: list[JsonValue] = []
    for group in ("properties", "$defs"):
        section = schema.get(group)
        if isinstance(section, dict):
            children.extend(section.values())
    for group in ("oneOf", "anyOf", "allOf", "prefixItems"):
        section = schema.get(group)
        if isinstance(section, list):
            children.extend(section)
    children.append(schema.get("items"))
    for child in children:
        if isinstance(child, dict):
            _validate_ui(child)


class Manifest(Inputs):
    id: Identifier
    name: str
    author: str
    category: str
    kind: Literal["application", "system"]
    sdk: str
    entry_point: EntryName
    version: str
    source: str
    permissions: list[str]
    capabilities: list[str]
    dependencies: list[Identifier]
    actions: list[Action]
    environment_fields: dict[str, Identifier] = Field(default_factory=dict)
    settings_schema: Values
    input_schemas: dict[str, Values]
    pages: list[str] = Field(default_factory=list)
    settings_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_contract(self) -> Self:
        if (
            Version(SDK_VERSION) not in SpecifierSet(self.sdk)
            or Version(self.version).is_prerelease
        ):
            raise ValueError("Unsupported plugin version or SDK")
        source = urlsplit(self.source)
        if (
            source.scheme != "https"
            or not source.hostname
            or source.username is not None
            or source.password is not None
            or source.fragment
            or source.query
        ):
            raise ValueError("Expected a public HTTPS publisher source")
        metadata = [
            self.name,
            self.author,
            self.category,
            self.source,
            *self.permissions,
            *self.capabilities,
            *(a.label for a in self.actions),
        ]
        if any(not s or len(s) > 4096 or not s.isprintable() for s in metadata):
            raise ValueError("Invalid plugin display metadata")
        prefix = "SETUP_PLUGIN_" + self.id.upper().replace("-", "_") + "_"
        for schema in (self.settings_schema, *self.input_schemas.values()):
            _validate_ui(schema)
        properties = self.settings_schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("Expected named settings fields")
        for name, field in self.environment_fields.items():
            spec = properties.get(field)
            ui = spec.get("ui") if isinstance(spec, dict) else None
            if (
                not name.startswith(prefix)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,120}", name)
                or not isinstance(spec, dict)
                or spec.get("writeOnly")
                or (isinstance(ui, dict) and ui.get("secret"))
            ):
                raise ValueError("Invalid plugin environment declaration")
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)) or set(self.input_schemas) != set(ids):
            raise ValueError("Actions require unique identities and matching input schemas")
        if any(set(a.permissions) - set(self.permissions) for a in self.actions):
            raise ValueError("Action requests undeclared permissions")
        return self


@runtime_checkable
class Installable(Protocol):
    async def install(self, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result: ...


@runtime_checkable
class Configurable(Protocol):
    async def configure(self, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result: ...


@runtime_checkable
class Uninstallable(Protocol):
    async def uninstall(self, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result: ...


class Backend:
    """Commands advertised by a plugin require matching implementations."""

    settings_model: ClassVar[type[PluginSettings]] = PluginSettings
    inputs: ClassVar[dict[str, type[Inputs]]] = {}

    async def inspect(self, settings: PluginSettings) -> Result:
        raise NotImplementedError

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        raise NotImplementedError

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        raise NotImplementedError

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        return []


class Mutation(Inputs):
    holdings: tuple[Resource, ...] = ()
    action_id: ActionId
    settings: Values
    inputs: Values
    plan: Plan
