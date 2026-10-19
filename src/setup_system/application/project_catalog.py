"""Pure schema for the owned project catalog, without filesystem inspection."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ProjectState = Literal["existing", "planned", "sourcePending", "optional"]
OwnerGroup = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*$")]


class ProjectRecord(BaseModel):
    """Catalog input; resolved paths and current Git state are separate values."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str
    state: ProjectState
    legacy_path: str | None = Field(default=None, alias="legacyPath")
    origin: str | None = None
    review_owner: Annotated[bool, Field(strict=True)] = Field(default=False, alias="reviewOwner")


class ProjectCatalog(BaseModel):
    """Versioned TOML contract; cross-record path authorization stays in the planner."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = Field(alias="schemaVersion")
    projects_root: str = Field(alias="projectsRoot")
    owner_groups: list[OwnerGroup] = Field(alias="ownerGroups")
    project: list[ProjectRecord]
