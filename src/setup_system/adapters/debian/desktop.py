"""Debian package backend for the explicitly requested desktop profile."""

from __future__ import annotations

import platform
import subprocess

HYPRLAND_VERSION = "0.55.2+ds-1~bpo13+1"
BASE_PACKAGES = (
    "waybar",
    "wofi",
    "mako-notifier",
    "foot",
    "wl-clipboard",
    "grim",
    "slurp",
    "brightnessctl",
    "cliphist",
    "libnotify-bin",
    "util-linux",
    "less",
    "network-manager",
    "network-manager-gnome",
    "pavucontrol",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "xdg-desktop-portal",
    "xdg-desktop-portal-gtk",
    "xwayland",
    "fontconfig",
    "fonts-noto-core",
    "fonts-noto-color-emoji",
    "qt6-wayland",
    "qtwayland5",
    "polkitd",
    "power-profiles-daemon",
)
BACKPORT_PACKAGES = (
    "hyprlock",
    "hypridle",
    "hyprpolkitagent",
    "xdg-desktop-portal-hyprland",
)


def package_arguments() -> list[str]:
    """Reject other OS/releases and leave repository selection to the administrator."""
    os_release = platform.freedesktop_os_release()
    if os_release.get("ID") != "debian" or os_release.get("VERSION_ID") != "13":
        raise ValueError("Hyprland packages require Debian 13 with official trixie-backports")
    if platform.machine() not in {"x86_64", "aarch64"}:
        raise ValueError("Desktop profile reviewed for amd64/arm64 only")
    return [
        f"hyprland={HYPRLAND_VERSION}",
        *(f"{name}/trixie" for name in BASE_PACKAGES),
        *(f"{name}/trixie-backports" for name in BACKPORT_PACKAGES),
    ]


def install_packages() -> None:
    """Simulate first; refuse removals and never change APT sources or restart the DM."""
    packages = package_arguments()
    command = ["apt-get", "install", "--no-remove", *packages]
    subprocess.run(["apt-get", "--simulate", *command[1:]], check=True, timeout=120)
    subprocess.run(["sudo", *command], check=True)
