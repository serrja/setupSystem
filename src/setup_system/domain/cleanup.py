"""Conservative package removal policy and the released candidate schema."""

from collections.abc import Mapping
from typing import Annotated

from pydantic import Field, StrictBool, StrictInt

from setup_system.domain.catalog import PackageName
from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import FrozenMap


class CleanupCandidate(ContractModel):
    requires_verified_editor: StrictBool = False


class CleanupCatalogDocument(ContractModel):
    schema_version: Annotated[StrictInt, Field(ge=1, le=1)]
    candidates: Annotated[FrozenMap[PackageName, CleanupCandidate], Field(min_length=1)]


class CleanupPlan(ContractModel):
    requested: frozenset[str]
    removed: frozenset[str]
    essential: frozenset[str]
    simulation: str


PROTECTED_PACKAGES = frozenset(
    {
        "apt",
        "dpkg",
        "sudo",
        "systemd",
        "systemd-sysv",
        "openssh-server",
        "openssh-client",
        "cryptsetup",
        "cryptsetup-initramfs",
        "initramfs-tools",
        "grub-common",
        "grub-pc",
        "grub-efi-amd64",
        "grub-efi-arm64",
        "linux-image-amd64",
        "linux-image-arm64",
        "network-manager",
        "iproute2",
        "hyprland",
        "greetd",
        "neovim",
        "python3",
    }
)


def validate_removals(
    requested: frozenset[str],
    removed: frozenset[str],
    essential: frozenset[str],
    *,
    editor_verified: bool,
    catalog: Mapping[str, CleanupCandidate],
) -> None:
    """Reject every unexpected dependency removal, including seemingly optional ones."""
    if not requested or not requested <= catalog.keys():
        raise ValueError("Only explicitly selected cleanup candidates are allowed")
    if removed != requested:
        raise ValueError("APT removal closure changed; review a new plan")
    if removed & (PROTECTED_PACKAGES | essential):
        raise ValueError("Essential, Protected or workstation package removal is forbidden")
    if not editor_verified and any(catalog[p].requires_verified_editor for p in removed):
        raise ValueError("A working Neovim fallback and EDITOR/VISUAL must be verified first")
