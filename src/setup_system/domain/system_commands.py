"""Commands accepted on both sides of the authenticated system boundary."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, TypeAdapter, model_validator

from setup_system.domain.catalog import ComponentID, PackageName
from setup_system.domain.contracts import ContractModel
from setup_system.domain.login import LoginConsent
from setup_system.domain.manifest_types import Digest
from setup_system.domain.paths import XdgPaths
from setup_system.domain.settings import SystemSettings

RegionalKind = Literal["timezone", "locale", "keyboard"]


class SystemOptions(SystemSettings):
    schema_version: Literal[1]
    hyprland: StrictBool
    graphical_login: StrictBool
    plymouth: StrictBool
    xdg: XdgPaths | None = None
    expected_system_sha256: Digest | None = None


class SystemCommand(ContractModel):
    action: Literal["base", "system_settings"]
    settings: SystemOptions

    @model_validator(mode="after")
    def selected_features(self) -> Self:
        if self.action != "system_settings" and not self.settings.hyprland:
            raise ValueError("An explicit Hyprland selection is required")
        return self


class LoginOptions(SystemOptions):
    confirmed: LoginConsent


class LoginCommand(ContractModel):
    action: Literal["login"] = "login"
    settings: LoginOptions

    @model_validator(mode="after")
    def selected(self) -> Self:
        if not self.settings.hyprland or not self.settings.graphical_login:
            raise ValueError("Graphical login and Hyprland must both be selected")
        return self


class RegionalOptions(ContractModel):
    value: Annotated[str, Field(pattern=r"^[A-Za-z0-9_+,./-]{1,100}$")]
    before: Annotated[str, Field(max_length=128)] | None = None
    packages: tuple[PackageName, ...] = ()
    expected_system_sha256: Digest | None = None


class RegionalCommand(ContractModel):
    action: RegionalKind
    settings: RegionalOptions

    @model_validator(mode="after")
    def valid_selection(self) -> Self:
        SystemSettings.model_validate({self.action: self.settings.value})
        return self


class BootOptions(ContractModel):
    identity: Digest
    packages: tuple[PackageName, ...] = ()
    expected_system_sha256: Digest | None = None


class BootCommand(ContractModel):
    action: Literal["plymouth"]
    settings: BootOptions


class PackageOptions(ContractModel):
    packages: tuple[PackageName, ...]
    happ: StrictBool = False
    external_debs: tuple[ComponentID, ...] = ()
    expected_happ_version: Annotated[str, Field(pattern=r"^[A-Za-z0-9.+:~_-]{1,200}$")] | None = (
        None
    )

    @model_validator(mode="after")
    def current_version_for_selected_happ(self) -> Self:
        if not (self.packages or self.external_debs or self.happ):
            raise ValueError("Select a package or reviewed external artifact")
        if self.expected_happ_version is not None and not self.happ:
            raise ValueError("Unexpected current Happ version")
        if len(set(self.packages)) != len(self.packages) or len(set(self.external_debs)) != len(
            self.external_debs
        ):
            raise ValueError("Duplicate package selection")
        return self


class PackagesCommand(ContractModel):
    action: Literal["workspace_packages"]
    settings: PackageOptions


class CleanupOptions(ContractModel):
    packages: tuple[PackageName, ...]
    mode: Literal["remove", "purge", "autoremove"]
    closure: tuple[PackageName, ...]
    xdg: XdgPaths | None = None


class CleanupCommand(ContractModel):
    action: Literal["cleanup"]
    settings: CleanupOptions


type PrivilegedCommand = Annotated[
    LoginCommand | SystemCommand | RegionalCommand | BootCommand | PackagesCommand | CleanupCommand,
    Field(discriminator="action"),
]
COMMAND = TypeAdapter(PrivilegedCommand)


class ReleaseIdentity(ContractModel):
    release: Annotated[str, Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?$")]
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
