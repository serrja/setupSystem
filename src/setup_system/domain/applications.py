"""Validate the finite native source declarations shipped with the workspace."""

from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from setup_system.domain.contracts import ContractModel

Package = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.+-]*$")]
ProjectName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")]
NpmName = Annotated[str, Field(pattern=r"^(@[a-z0-9-]+/)?[a-z0-9][a-z0-9._-]*$")]
AssetName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+{}-]*\.deb$")]
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Application(ContractModel):
    kind: Literal["app", "optional", "language"]
    title: str = Field(min_length=1, max_length=120)
    packages: tuple[Package, ...] = ()
    npm: tuple[NpmName, ...] = ()
    pypi: tuple[ProjectName, ...] = ()
    github: str | None = Field(None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    asset: dict[Literal["amd64", "arm64"], AssetName] = Field(default_factory=dict)

    @model_validator(mode="after")
    def executable_source(self) -> Self:
        if self.github:
            if not self.asset or self.packages or self.npm or self.pypi:
                raise ValueError("An upstream Debian archive requires one reviewed source")
            for name in self.asset.values():
                suffix = name.replace("{version}", "")
                if "{" in suffix or "}" in suffix:
                    raise ValueError("Unknown archive name field")
        elif self.asset or not self.packages:
            raise ValueError("Choose a native package source")
        return self


APPLICATIONS = TypeAdapter(dict[Slug, Application])


def applications(data: object) -> dict[str, Application]:
    catalog = APPLICATIONS.validate_python(data)
    for key, item in catalog.items():
        if item.github and key not in {"happ", "obsidian"}:
            raise ValueError("Unknown upstream Debian adapter")
    return catalog
