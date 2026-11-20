"""Pure official-Debian package selection for the managed Hyprland profile."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import cast

from setup_system.capabilities import load_policy


def profile_capabilities(root: Path | None = None) -> dict[str, object]:
    """Load the shared profile matrix without package, network or privilege operations."""
    return cast(dict[str, object], load_policy(root).document()["hyprland"])


HYPRLAND_VERSION = load_policy().hyprland.version_pin


def package_arguments(
    *,
    os_release: dict[str, str] | None = None,
    architecture: str | None = None,
    root: Path | None = None,
    graphical_login: bool = True,
) -> list[str]:
    """Select one compatible package set; the privileged system adapter owns execution."""
    capabilities = load_policy(root)
    profile = capabilities.hyprland
    release = platform.freedesktop_os_release() if os_release is None else os_release
    machine = platform.machine() if architecture is None else architecture
    machine = capabilities.normalize_architecture(machine)
    if (
        release.get("ID") != profile.distribution
        or release.get("VERSION_ID") != profile.version
        or release.get("VERSION_CODENAME") != profile.codename
        or machine not in profile.architectures
    ):
        raise ValueError(
            f"Hyprland requires {profile.distribution} {profile.version} "
            f"({profile.codename}) on {', '.join(profile.architectures)} "
            f"with signed official {profile.backports}"
        )
    backports = profile.backport_packages
    return [
        f"hyprland={profile.version_pin}",
        *(
            package
            for package in profile.base_packages
            if graphical_login or package not in {"greetd", "gtkgreet", "cage"}
        ),
        *(f"{name}={version}" for name, version in backports.items()),
    ]
