"""The release capability matrix and its cross-field invariants."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import FrozenMap, UniqueEntries, Version


class CapabilityStatus(StrEnum):
    IMPLEMENTED = "implemented"
    UNSUPPORTED = "unsupported"


Architecture = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]*$")]
DebianVersion = Annotated[str, Field(pattern=r"^[0-9]+$")]
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
PackageName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9+.-]*$")]
PackageVersion = Annotated[str, Field(pattern=r"^[A-Za-z0-9.+:~_-]+$")]
CommandName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")]


class DebianCapabilities(ContractModel):
    versions: UniqueEntries[DebianVersion]
    architectures: UniqueEntries[Architecture]
    codenames: FrozenMap[DebianVersion, Name]
    apt_architectures: FrozenMap[Architecture, Architecture]
    status: CapabilityStatus


class HyprlandCapabilities(ContractModel):
    distribution: Literal["debian"]
    version: DebianVersion
    codename: Name
    apt_architectures: UniqueEntries[Architecture]
    architectures: UniqueEntries[Architecture]
    version_pin: PackageVersion
    upstream_version: Version
    backports: Name
    system_python: Annotated[str, Field(pattern=r"^3\.[1-9][0-9]*$")]
    init_system: Name
    init_version: Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]+)*$")]
    commands: UniqueEntries[CommandName]
    base_packages: UniqueEntries[PackageName]
    backport_packages: FrozenMap[PackageName, PackageVersion]


class Unsupported(ContractModel):
    reason: Annotated[str, Field(min_length=1, max_length=2048, pattern=r"^[^\p{Cc}]+$")]


class CapabilityPolicy(ContractModel):
    architectures: FrozenMap[Architecture, Architecture]
    debian: DebianCapabilities
    hyprland: HyprlandCapabilities
    unsupported: Unsupported

    @model_validator(mode="after")
    def matrix_agrees(self) -> Self:
        deb, gui, aliases = self.debian, self.hyprland, self.architectures
        apt = deb.apt_architectures
        if set(apt) != set(deb.architectures) or len(set(apt.values())) != len(apt):
            raise ValueError("Incomplete or ambiguous Debian architecture mapping")
        if (
            any(key in apt or value not in apt for key, value in aliases.items())
            or any(aliases.get(value, value) != key for key, value in apt.items())
            or not set(gui.architectures) <= apt.keys()
            or tuple(apt[key] for key in gui.architectures) != gui.apt_architectures
        ):
            raise ValueError("Inconsistent package architecture mapping")
        if (
            not set(deb.versions) <= deb.codenames.keys()
            or gui.version not in deb.versions
            or deb.codenames[gui.version] != gui.codename
            or gui.backports != gui.codename + "-backports"
        ):
            raise ValueError("Debian codename/backports disagree with the selected version")
        if not gui.backport_packages:
            raise ValueError("Missing Debian backport package pins")
        return self

    @property
    def unsupported_reason(self) -> str:
        return self.unsupported.reason

    def normalize_architecture(self, architecture: str) -> str:
        return self.architectures.get(architecture, architecture)

    def debian_architecture(self, architecture: str) -> str:
        try:
            return self.debian.apt_architectures[self.normalize_architecture(architecture)]
        except KeyError:
            raise ValueError("No exact Debian package architecture matches this target") from None

    def debian_codename(self, version: str) -> str:
        try:
            return self.debian.codenames[version]
        except KeyError:
            raise ValueError("No Debian codename matches this version") from None
