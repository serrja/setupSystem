import re
from typing import Literal, Self
from urllib.parse import urlsplit

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import Field, JsonValue, model_validator

from setup_system_sdk import SDK_VERSION
from setup_system_sdk.enums.operation import ActionMode
from setup_system_sdk.enums.permissions import Permission
from setup_system_sdk.enums.ui import StorageScope
from setup_system_sdk.schemas.common import ActionId, EntryName, Identifier, Inputs, Values


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
    storage: StorageScope = StorageScope.SETTINGS


class Action(Inputs):
    cleanup_phase: Literal["remote", "local", "program"] | None = None
    id: ActionId
    label: str
    page: Literal["actions", "accounts"] = "actions"
    target: Identifier | None = None
    permissions: list[Permission] = Field(default_factory=list)
    supports_rollback: bool = False
    mode: ActionMode
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
    permissions: list[Permission]
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
        for action in self.actions:
            if (action.mode in (ActionMode.MUTATION, ActionMode.AUTHENTICATED_CHECK)) != (
                action.target is not None
            ):
                raise ValueError("A mutation needs an explicit reviewed target")
        ids = [a.id for a in self.actions]
        if len(ids) != len(set(ids)) or set(self.input_schemas) != set(ids):
            raise ValueError("Actions require unique identities and matching input schemas")
        if any(set(a.permissions) - set(self.permissions) for a in self.actions):
            raise ValueError("Action requests undeclared permissions")
        return self
