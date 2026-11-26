"""Pure schema for the owned project catalog, without filesystem inspection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from packaging.version import InvalidVersion, Version
from pydantic import ConfigDict, Field, field_validator, model_validator

from setup_system.execution.contracts import ContractModel

OwnerGroup = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*$")]


def _normalized_version(value: str) -> Version:
    try:
        version = Version(value)
    except InvalidVersion:
        raise ValueError("Template version must use PEP 440") from None
    if str(version) != value or any(character in value for character in "+!"):
        raise ValueError("Template release versions must be normalized without local/epoch parts")
    return version


def python_version_for_git_tag(tag: str) -> str:
    """Read current GitHub tags and legacy normalized PEP 440 template tags."""
    match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)-dev\.(0|[1-9][0-9]*)", tag)
    if match:
        value = f"{match[1]}.dev{match[2]}"
    elif tag.startswith("v") and "-" not in tag:
        value = tag[1:]
    else:
        raise ValueError("Template tag must be a versioned GitHub tag")
    return str(_normalized_version(value))


class TemplateVersion(ContractModel):
    """Reviewed annotated tag and its immutable commit, never a branch selector."""

    version: str
    tag: str
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]

    @model_validator(mode="after")
    def canonical_tag(self) -> TemplateVersion:
        _normalized_version(self.version)
        if python_version_for_git_tag(self.tag) != self.version:
            raise ValueError("Template tag must match its native version")
        return self


class TemplateMetadata(ContractModel):
    """Trusted template choices belonging to an existing project record."""

    model_config = ConfigDict(populate_by_name=True)

    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*$")]
    recommended_version: str | None = Field(default=None, alias="recommendedVersion")
    versions: tuple[TemplateVersion, ...] = ()

    @model_validator(mode="after")
    def reviewed_recommendation(self) -> TemplateMetadata:
        names = [item.version for item in self.versions]
        if len(names) != len(set(names)):
            raise ValueError("Template versions must be unique")
        if names and self.recommended_version not in names:
            raise ValueError("Recommended version must be a reviewed version")
        if not names and self.recommended_version is not None:
            raise ValueError("Recommended version is unavailable")
        return self


class CatalogTemplate(ContractModel):
    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*$")]


class ProjectRecord(ContractModel):
    """Catalog input; resolved paths and current Git state are separate values."""

    id: Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*/(?:[A-Za-z][A-Za-z0-9]*|\.github)$")]
    origin: str
    template: CatalogTemplate | None = None

    @model_validator(mode="after")
    def canonical_origin(self) -> ProjectRecord:
        if self.origin not in {
            f"https://github.com/{self.id}.git",
            f"git@github.com:{self.id}.git",
        }:
            raise ValueError("Pinned source origin must match its canonical GitHub project")
        return self


class ProjectCatalog(ContractModel):
    """Versioned TOML contract; cross-record path authorization stays in the planner."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = Field(alias="schemaVersion")
    projects_root: str = Field(alias="projectsRoot")
    owner_groups: tuple[OwnerGroup, ...] = Field(alias="ownerGroups")
    project: tuple[ProjectRecord, ...]

    @field_validator("projects_root")
    @classmethod
    def home_relative(cls, value: str) -> str:
        if not value.startswith("$HOME/"):
            raise ValueError("Project paths must start with literal $HOME/")
        if (
            any(part in {"", ".", ".."} for part in value[6:].split("/"))
            or "\\" in value
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Project path has an unsafe component")
        return value

    @field_validator("owner_groups")
    @classmethod
    def declared_owners(cls, owners: tuple[str, ...]) -> tuple[str, ...]:
        if not owners or len(set(owners)) != len(owners):
            raise ValueError("Owner groups must be unique")
        return owners

    @model_validator(mode="after")
    def unique_records(self) -> ProjectCatalog:
        ids = [record.id for record in self.project]
        if len(set(ids)) != len(ids) or any(
            project.split("/")[0] not in self.owner_groups for project in ids
        ):
            raise ValueError("Invalid or repeated project ID")
        aliases = [record.template.alias for record in self.project if record.template is not None]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Duplicate template alias")
        return self


class Project(ContractModel):
    id: str
    target: Path
    origin: str | None


class ProjectLayout(ContractModel):
    projects_root: Path
    owner_groups: tuple[str, ...]
    projects: tuple[Project, ...]
