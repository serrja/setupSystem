"""Validate the package-owned Snap Store catalog and the user's selection."""

from functools import cache
from importlib.resources import files as package_files
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator
from setup_system_sdk.schemas.common import Inputs

SnapName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
StoreID = Annotated[str, Field(pattern=r"^[0-9A-Za-z]{32}$")]
STORE = "https://api.snapcraft.io/v2/snaps/info/"


class Entry(Inputs):
    id: SnapName
    title: str = Field(min_length=1, max_length=100)
    source: Literal["https://api.snapcraft.io/v2/snaps/info/"]
    snap_id: StoreID
    publisher_id: StoreID
    publisher: str = Field(min_length=1, max_length=100)
    channel: Literal["latest/stable"]
    confinement: Literal["strict"]
    platforms: tuple[Literal["amd64", "arm64"], ...] = Field(min_length=1, max_length=2)
    removal: str = Field(min_length=1, max_length=1000)


@cache
def catalog() -> dict[str, Entry]:
    rows = TypeAdapter(list[Entry]).validate_json(
        (
            Path(str(package_files("setup_plugin_snap").joinpath("resources"))) / "catalog.json"
        ).read_bytes()
    )
    result = {row.id: row for row in rows}
    if not rows or len(result) != len(rows) or len({r.snap_id for r in rows}) != len(rows):
        raise ValueError("The Snap catalog contains ambiguous entries")
    return result


CatalogApplication = Annotated[SnapName, Field(json_schema_extra={"enum": sorted(catalog())})]


class Selection(Inputs):
    applications: list[CatalogApplication] = Field(
        min_length=1,
        max_length=24,
        title="Catalog applications",
        description="Select one or more approved applications from the Snap Store catalog.",
        json_schema_extra={"ui": {"widget": "selection_list", "choice_provider": "applications"}},
    )

    @field_validator("applications")
    @classmethod
    def catalog_members(cls, names: list[str]) -> list[str]:
        if len(names) != len(set(names)) or not set(names) <= catalog().keys():
            raise ValueError("Select distinct applications from the packaged catalog")
        return sorted(names)
