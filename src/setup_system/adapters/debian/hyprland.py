"""Pure official-Debian package selection for the managed Hyprland profile."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import cast

from setup_system.planning import load_capabilities


def profile_capabilities(root: Path | None = None) -> dict[str, object]:
    """Load the shared profile matrix without package, network or privilege operations."""
    return cast(dict[str, object], load_capabilities(root)["hyprland"])


HYPRLAND_VERSION = str(profile_capabilities()["version_pin"])


def package_arguments(
    *,
    os_release: dict[str, str] | None = None,
    architecture: str | None = None,
    root: Path | None = None,
) -> list[str]:
    """Select one compatible package set; the privileged bootstrap owns execution."""
    capabilities = load_capabilities(root)
    profile = cast(dict[str, object], capabilities["hyprland"])
    aliases = cast(dict[str, str], capabilities["architectures"])
    release = platform.freedesktop_os_release() if os_release is None else os_release
    machine = platform.machine() if architecture is None else architecture
    machine = aliases.get(machine, machine)
    if (
        release.get("ID") != profile["distribution"]
        or release.get("VERSION_ID") != profile["version"]
        or release.get("VERSION_CODENAME") != profile["codename"]
        or machine not in cast(list[str], profile["architectures"])
    ):
        raise ValueError(
            f"Hyprland requires {profile['distribution']} {profile['version']} "
            f"({profile['codename']}) on {', '.join(cast(list[str], profile['architectures']))} "
            f"with signed official {profile['backports']}"
        )
    backports = cast(dict[str, str], profile["backport_packages"])
    return [
        f"hyprland={profile['version_pin']}",
        *cast(list[str], profile["base_packages"]),
        *(f"{name}={version}" for name, version in backports.items()),
    ]
