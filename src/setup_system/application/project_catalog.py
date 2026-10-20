"""Pure schema for the owned project catalog, without filesystem inspection."""

from __future__ import annotations

from typing import Annotated, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

ProjectState = Literal["existing", "planned", "sourcePending", "optional"]
OwnerGroup = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*$")]


class TemplateVersion(BaseModel):
    """Reviewed annotated tag and its immutable commit, never a branch selector."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    version: str
    tag: str
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]

    @model_validator(mode="after")
    def canonical_tag(self) -> TemplateVersion:
        try:
            normalized = str(Version(self.version))
        except InvalidVersion:
            raise ValueError("Template version must use PEP 440") from None
        if normalized != self.version or any(character in self.version for character in "+!"):
            raise ValueError(
                "Template release versions must be normalized without local/epoch parts"
            )
        if self.tag != f"v{self.version}":
            raise ValueError("Template tag must be v followed by its version")
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


class ProjectRecord(BaseModel):
    """Catalog input; resolved paths and current Git state are separate values."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str
    state: ProjectState
    legacy_path: str | None = Field(default=None, alias="legacyPath")
    origin: str | None = None
    template: TemplateMetadata | None = None
    review_owner: Annotated[bool, Field(strict=True)] = Field(default=False, alias="reviewOwner")


class ProjectCatalog(BaseModel):
    """Versioned TOML contract; cross-record path authorization stays in the planner."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = Field(alias="schemaVersion")
    projects_root: str = Field(alias="projectsRoot")
    owner_groups: list[OwnerGroup] = Field(alias="ownerGroups")
    project: list[ProjectRecord]
