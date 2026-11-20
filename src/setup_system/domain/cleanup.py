"""Pure conservative package removal policy."""

import re
import tomllib
from pathlib import Path
from typing import TypedDict


class CleanupCandidate(TypedDict):
    requires_verified_editor: bool


def cleanup_catalog() -> dict[str, CleanupCandidate]:
    path = Path(__file__).resolve().parents[1] / "resources/catalogs/optional_cleanup.toml"
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    entries = data.get("candidates")
    if data.get("schema_version") != 1 or not isinstance(entries, dict) or not entries:
        raise ValueError("Invalid cleanup catalog")
    result: dict[str, CleanupCandidate] = {}
    for package, metadata in entries.items():
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package)
            or not isinstance(metadata, dict)
            or set(metadata) - {"requires_verified_editor"}
        ):
            raise ValueError("Unexpected cleanup candidate")
        required = metadata.get("requires_verified_editor", False)
        if not isinstance(required, bool):
            raise ValueError("Invalid editor requirement")
        result[package] = {"requires_verified_editor": required}
    return result


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
) -> None:
    """Reject every unexpected dependency removal, including seemingly optional ones."""
    catalog = cleanup_catalog()
    if not requested or not requested <= catalog.keys():
        raise ValueError("Only explicitly selected cleanup candidates are allowed")
    if removed != requested:
        raise ValueError("APT removal closure changed; review a new plan")
    if removed & (PROTECTED_PACKAGES | essential):
        raise ValueError("Essential, Protected or workstation package removal is forbidden")
    if not editor_verified and any(catalog[p]["requires_verified_editor"] for p in removed):
        raise ValueError("A working Neovim fallback and EDITOR/VISUAL must be verified first")
