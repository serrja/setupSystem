from pathlib import Path
from typing import Literal

from pydantic import Field
from setup_system_sdk.schemas.common import Inputs


class Project(Inputs):
    project: str = Field(
        description="Choose the managed checkout that this action will inspect or update.",
        title="Active project",
        max_length=128,
        pattern=r"^[a-z][A-Za-z0-9]*/(?:[A-Za-z][A-Za-z0-9]*|\.github)$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "projects"}},
    )


class CatalogTemplate(Inputs):
    template: str = Field(
        description="Select a template from the packaged project catalog.",
        pattern=r"^[a-z][a-z0-9]+$",
        max_length=32,
        title="Catalog template",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "templates"}},
    )


class Template(CatalogTemplate):
    version: str = Field(
        default="",
        max_length=80,
        pattern=r"^[a-zA-Z0-9.+-]*$",
        title="Release",
        description="Leave empty to resolve the latest stable annotated release.",
    )


class Destination(Template):
    destination: Path = Field(
        title="Project directory", description="A directory inside your HOME."
    )


class Creation(Destination):
    project_name: str = Field(
        description="Name the project directory that will be created.",
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$",
        title="Project name",
    )
    python_profile: Literal["library", "native", "service"] | None = Field(
        description="Choose the project type expected by the upstream Python template.",
        default=None,
        title="Python template profile",
    )
