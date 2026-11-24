"""Bounded facts transported between an inspected native plan and its application."""

from collections.abc import ItemsView
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self, cast

from pydantic import Field, JsonValue, StrictBool, StrictInt, model_validator

from setup_system.domain.actions import AbsoluteName, Line, RelativeName, Token
from setup_system.domain.contracts import ContractModel
from setup_system.domain.displays import AdvertisedMode
from setup_system.domain.intentions import BaseOptions, Section, WorkspaceOptions
from setup_system.domain.login import LoginConsent
from setup_system.domain.manifest_types import Commit, Digest
from setup_system.domain.receipts import FileState, RunId

Mode = Annotated[StrictInt, Field(ge=0, le=0o777)]
Natural = Annotated[StrictInt, Field(ge=0)]
type Lines = Annotated[tuple[Line, ...], Field(max_length=4096)]
type Url = Annotated[Line, Field(pattern=r"^https://[^\s]+$")]


class XdgPaths(ContractModel):
    config: AbsoluteName
    data: AbsoluteName
    state: AbsoluteName
    cache: AbsoluteName


class OutputModes(ContractModel):
    output: Token
    modes: tuple[AdvertisedMode, ...]


class ManagedPreview(ContractModel):
    changes: tuple[RelativeName, ...] = ()
    before: dict[RelativeName, FileState | None] = Field(default_factory=dict)
    after: dict[RelativeName, tuple[Digest, Mode]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if set(self.before) != set(self.after) or not set(self.changes) <= set(self.before):
            raise ValueError("Managed-file preconditions and targets must agree")
        return self


class UnlockBefore(ContractModel):
    identity: Digest | None
    packages: tuple[Token, ...] = ()
    blockers: Lines = ()


class SystemBefore(ContractModel):
    timezone: str | None = Field(default=None, max_length=128)
    locale: str | None = Field(default=None, max_length=128)
    keyboard: str | None = Field(default=None, max_length=128)
    login: LoginConsent | None = None
    unlock: UnlockBefore | None = None
    unlock_current: StrictBool | None = None
    firefox: tuple[Natural, Natural, Mode, Digest] | Literal["root-only"] | None = None


class HappUnit(ContractModel):
    LoadState: Literal["not-found", "loaded"]
    ActiveState: Literal["active", "inactive"]
    FragmentPath: AbsoluteName | Literal[""] = ""


class HappBefore(ContractModel):
    unit: HappUnit
    installed: Token | None


class DebianAsset(ContractModel):
    name: Annotated[Token, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._+~-]*\.deb$")]
    version: Token
    url: Url
    checksum: Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
    before: HappBefore | None = None


class PackageArtifact(ContractModel):
    filename: Annotated[Token, Field(pattern=r"^[A-Za-z0-9_.+-]+$")]
    url: Url
    checksum: Annotated[str, Field(pattern=r"^(sha256:[a-f0-9]{64}|sha512:[a-f0-9]{128})$")]


class PackageTool(ContractModel):
    manager: Literal["npm", "pypi"]
    version: Token
    current: Token | Literal[""] | None
    artifact: PackageArtifact


class GitTool(ContractModel):
    manager: Literal["git"]
    version: Commit
    current: Commit | Literal[""]
    repo: Url
    path: AbsoluteName


class Observations(ContractModel):
    """Expose a detached JSON view to widgets while keeping wire validation explicit."""

    needs_system: StrictBool = False
    needs_user: StrictBool = False

    def get(self, key: str, default: JsonValue = None) -> JsonValue:
        return cast(JsonValue, self.model_dump(mode="json", exclude_unset=True).get(key, default))

    def __getitem__(self, key: str) -> JsonValue:
        return cast(JsonValue, self.model_dump(mode="json", exclude_unset=True)[key])

    def items(self) -> ItemsView[str, JsonValue]:
        return self.model_dump(mode="json", exclude_unset=True).items()


class NativeObservations(Observations):
    section: Section | None = None
    selection: BaseOptions | WorkspaceOptions | None = None
    uid: Natural | None = None
    username: Line | None = None
    home: AbsoluteName | None = None
    xdg: XdgPaths | None = None
    architecture: Line | None = None
    distribution: Line | None = None
    outputs: tuple[OutputModes, ...] = ()
    blockers: Lines = ()
    timezones: Lines = ()
    keyboards: Lines = ()
    locales: Lines = ()
    backups: tuple[RunId, ...] = ()
    last_chatgpt_source_check: str = Field(default="", max_length=4096)
    previous_package_operation: str = Field(default="", max_length=4096)
    debs: dict[Literal["happ", "obsidian"], DebianAsset] = Field(default_factory=dict)
    tools: dict[Token, Annotated[PackageTool | GitTool, Field(discriminator="manager")]] = Field(
        default_factory=dict
    )
    boot: SystemBefore = Field(default_factory=SystemBefore)
    files: ManagedPreview = Field(default_factory=ManagedPreview)

    @model_validator(mode="after")
    def xdg_inside_home(self) -> Self:
        if self.home and self.xdg:
            home = PurePosixPath(self.home)
            if any(
                home not in PurePosixPath(path).parents for path in self.xdg.model_dump().values()
            ):
                raise ValueError("User directories must be inside the inspected HOME")
        return self


class ProjectObservations(Observations):
    projects: dict[Line, AbsoluteName] = Field(default_factory=dict)
    templates: dict[Line, Url] = Field(default_factory=dict)
    git_profiles: dict[Line, AbsoluteName] = Field(default_factory=dict)
    versions: tuple[Token, ...] = ()
    recommended: Token | None = None
    identity: Digest | dict[Line, str] | None = None
    before: dict[AbsoluteName, FileState | None] = Field(default_factory=dict)
    version: Token | None = None
    tag: Token | None = None
    source_commit: Commit | None = None
    source: Url | None = None
    destination: AbsoluteName | None = None
    source_versions: dict[Token, Commit] = Field(default_factory=dict)
    before_head: Commit | None = None
    target: AbsoluteName | None = None
    archive: Token | None = None
    sha256: Digest | None = None
    checksum_sha256: Digest | None = None
    install_mode: Mode | None = None
    directory: AbsoluteName | None = None
    backup_id: RunId | None = None
