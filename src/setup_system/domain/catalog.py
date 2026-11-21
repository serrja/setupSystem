"""Immutable components in a reviewed release and their explicit installation handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, get_args

from pydantic import Field, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import FrozenMap

if TYPE_CHECKING:
    from setup_system.domain.settings import UserSettings


LanguageProfile = Literal["python", "typescript_vue", "dart_flutter", "cpp"]
Kind = Literal["app", "optional", "language"]
Handler = Literal[
    "apt", "happ", "neovim", "firefox", "codex", "catalog_deb", "python_tools", "language"
]
PackageName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9+.-]+$")]
ComponentID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]+$")]
DebianArchitecture = Literal["amd64", "arm64"]


class DebArtifact(ContractModel):
    url: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    version: Annotated[str, Field(pattern=r"^[0-9][0-9A-Za-z.+~-]{0,100}$")]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ComponentDefinition(ContractModel):
    kind: Kind
    title: Annotated[str, Field(min_length=1, max_length=200)]
    handler: Handler
    packages: tuple[PackageName, ...] = ()
    artifacts: FrozenMap[DebianArchitecture, DebArtifact] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_inputs(self) -> ComponentDefinition:
        if len(set(self.packages)) != len(self.packages):
            raise ValueError("Duplicate release package")
        if self.handler == "apt" and not self.packages:
            raise ValueError("The APT handler requires a reviewed package selection")
        if (self.handler in {"python_tools", "language"}) != (self.kind == "language"):
            raise ValueError("Language profiles require a language handler")
        if self.artifacts and self.handler != "catalog_deb":
            raise ValueError("Only the Debian artifact handler accepts external artifacts")
        return self


class Component(ContractModel):
    id: ComponentID
    kind: Kind
    title: str
    handler: Handler
    packages: tuple[PackageName, ...]
    blocker: str | None = None
    artifact: DebArtifact | None = None

    @model_validator(mode="after")
    def implemented_identity(self) -> Component:
        if self.handler in {"happ", "neovim", "firefox", "codex"} and (
            self.id != self.handler or self.kind != "app"
        ):
            raise ValueError("Application selection must match its built-in handler")
        if self.kind == "language" and (
            self.id not in get_args(LanguageProfile)
            or (self.id == "python") != (self.handler == "python_tools")
        ):
            raise ValueError("No language handler implements this profile")
        return self


class ComponentCatalog(ContractModel):
    entries: tuple[Component, ...]

    @model_validator(mode="after")
    def distinct_ids(self) -> ComponentCatalog:
        ids = [entry.id for entry in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("Repeated component ID")
        return self

    def components(self, kind: str | None = None) -> tuple[Component, ...]:
        return tuple(row for row in self.entries if kind is None or row.kind == kind)

    def validate_settings(self, settings: UserSettings) -> UserSettings:
        for kind, choices in (
            ("app", settings.apps),
            ("language", settings.language_profiles),
            ("optional", settings.optional_apps),
        ):
            if not set(choices) <= {entry.id for entry in self.components(kind)}:
                raise ValueError("Unknown catalog selection for " + kind)
        return settings

    def selected(self, settings: UserSettings) -> tuple[Component, ...]:
        self.validate_settings(settings)
        choices = {*settings.apps, *settings.optional_apps, *settings.language_profiles}
        return tuple(entry for entry in self.entries if entry.id in choices)
