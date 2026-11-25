"""Validate the finite native source declarations shipped with the workspace."""

from typing import Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from setup_system.domain.contracts import ContractModel
from setup_system.domain.intentions import BaseOptions

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
        if len(set(self.packages)) != len(self.packages):
            raise ValueError("Duplicate Debian package in application")
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


def applications(data: Any) -> dict[str, Application]:
    catalog = APPLICATIONS.validate_python(data)
    for key, item in catalog.items():
        if item.github and key not in {"happ", "obsidian"}:
            raise ValueError("Unknown upstream Debian adapter")
    return catalog


class StablePackages(ContractModel):
    stable: tuple[Package, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.stable)) != len(self.stable):
            raise ValueError("Duplicate stable Debian package")
        return self


class DesktopPackages(StablePackages):
    backports: tuple[Package, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_sources(self) -> Self:
        if len(set(self.backports)) != len(self.backports) or set(self.stable).intersection(
            self.backports
        ):
            raise ValueError("Conflicting stable or backports Debian package selections")
        return self


class BasePackages(ContractModel):
    hyprland: DesktopPackages
    login: StablePackages

    locale: StablePackages
    keyboard: StablePackages
    unlock_probes: dict[
        Package, Annotated[str, Field(pattern=r"^/usr/(?:[A-Za-z0-9_.+-]+/)*[A-Za-z0-9_.+-]+$")]
    ] = Field(min_length=1)

    @property
    def desktop(self) -> set[str]:
        return {*self.hyprland.stable, *(p + "/trixie-backports" for p in self.hyprland.backports)}

    def selected(self, intent: BaseOptions) -> set[str]:
        """Resolve the base selection against the reviewed Debian sources."""
        packages = set[str]()
        if intent.hyprland:
            packages.update(self.desktop)
        if intent.graphical_login:
            packages.update(self.login.stable)
        if intent.locale:
            packages.update(self.locale.stable)
        if intent.keyboard:
            packages.update(self.keyboard.stable)
        return packages

    def allowed(self) -> set[str]:
        """Enumerate the complete base package allowlist at the privilege boundary."""
        return {
            *self.desktop,
            *self.login.stable,
            *self.locale.stable,
            *self.keyboard.stable,
            *self.unlock_probes,
        }
