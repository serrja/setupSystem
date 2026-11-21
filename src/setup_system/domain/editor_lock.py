"""Release-pinned binaries, plugin revisions and language parsers for the editor."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Commit, Digest, FrozenMap, UniqueEntries, Version

Https = Annotated[str, Field(pattern=r"^https://[^\s]+$")]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Parser = Annotated[str, Field(pattern=r"^[a-z_]+$")]


class ArchiveRelease(ContractModel):
    version: Version
    linux_x86_64_url: Https
    linux_x86_64_sha256: Digest
    linux_arm64_url: Https | None = None
    linux_arm64_sha256: Digest | None = None

    @model_validator(mode="after")
    def complete_pair(self) -> Self:
        if (self.linux_arm64_url is None) != (self.linux_arm64_sha256 is None):
            raise ValueError("Incomplete arm64 archive provenance")
        return self

    def archive(self, arch: str) -> tuple[str, str] | None:
        if arch == "x86_64":
            return self.linux_x86_64_url, self.linux_x86_64_sha256
        if (
            arch == "arm64"
            and self.linux_arm64_url is not None
            and self.linux_arm64_sha256 is not None
        ):
            return self.linux_arm64_url, self.linux_arm64_sha256
        return None


class FlutterRelease(ArchiveRelease):
    revision: Commit


class Plugin(ContractModel):
    repo: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")]
    commit: Commit
    branch: ToolName | None = None


class EditorLock(ContractModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    version: Literal[1]
    neovim: ArchiveRelease
    plugins: FrozenMap[ToolName, Plugin]
    archive_tools: FrozenMap[ToolName, ArchiveRelease] = Field(alias="archiveTools")
    parsers: FrozenMap[ToolName, UniqueEntries[Parser]]
    flutter: FlutterRelease

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Invalid editor lock version")
        return value

    @model_validator(mode="after")
    def required_editor_features(self) -> Self:
        if (
            "base" not in self.parsers
            or "lazy" not in self.plugins
            or "tree-sitter" not in self.archive_tools
        ):
            raise ValueError("Incomplete editor lock")
        if any(not plugin.branch for name, plugin in self.plugins.items() if name != "lazy"):
            raise ValueError("Managed Lazy plugin requires a branch")
        return self
