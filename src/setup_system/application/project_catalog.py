"""Pure schema for the owned project catalog, without filesystem inspection."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

ProjectState = Literal["existing"]
OwnerGroup = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*$")]


def _normalized_version(value: str) -> Version:
    try:
        version = Version(value)
    except InvalidVersion:
        raise ValueError("Template version must use PEP 440") from None
    if str(version) != value or any(character in value for character in "+!"):
        raise ValueError("Template release versions must be normalized without local/epoch parts")
    return version


def git_tag_for_python_version(value: str) -> str:
    """Map a native dev version to the shared GitHub tag, retaining older formats."""
    version = _normalized_version(value)
    if (
        version.dev is not None
        and version.pre is None
        and version.post is None
        and len(version.release) == 3
    ):
        return f"v{version.base_version}-dev.{version.dev}"
    return "v" + value


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


class TemplateVersion(BaseModel):
    """Reviewed annotated tag and its immutable commit, never a branch selector."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    version: str
    tag: str
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]

    @model_validator(mode="after")
    def canonical_tag(self) -> TemplateVersion:
        _normalized_version(self.version)
        if python_version_for_git_tag(self.tag) != self.version:
            raise ValueError("Template tag must match its native version")
        return self


class TemplateMetadata(BaseModel):
    """Trusted template choices belonging to an existing project record."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
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


class PolicyMetadata(BaseModel):
    """Reviewed policy provenance; this metadata never loads or executes rules."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    document: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def relative_document(self) -> PolicyMetadata:
        path = PurePosixPath(self.document)
        if (
            path.is_absolute()
            or str(path) != self.document
            or ".." in path.parts
            or "\\" in self.document
            or path.suffix != ".md"
        ):
            raise ValueError("Policy document must be a safe relative Markdown path")
        return self


class ProjectRecord(BaseModel):
    """Catalog input; resolved paths and current Git state are separate values."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str
    state: ProjectState
    origin: str | None = None
    template: TemplateMetadata | None = None
    policy: PolicyMetadata | None = None

    @model_validator(mode="after")
    def canonical_policy_origin(self) -> ProjectRecord:
        if self.policy is not None and self.origin not in {
            f"https://github.com/{self.id}.git",
            f"git@github.com:{self.id}.git",
        }:
            raise ValueError("Policy origin must match its canonical GitHub project")
        return self


class ProjectCatalog(BaseModel):
    """Versioned TOML contract; cross-record path authorization stays in the planner."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = Field(alias="schemaVersion")
    projects_root: str = Field(alias="projectsRoot")
    owner_groups: list[OwnerGroup] = Field(alias="ownerGroups")
    project: list[ProjectRecord]
