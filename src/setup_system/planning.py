"""Read-only planning shared by application services and Textual."""

from __future__ import annotations

import platform as system_platform
from dataclasses import dataclass
from pathlib import Path

from setup_system.capabilities import CapabilityStatus, load_policy


@dataclass(frozen=True)
class Platform:
    distribution: str
    version: str
    architecture: str
    implemented: bool
    verified: bool
    reason: str


def load_capabilities(root: Path | None = None) -> dict[str, object]:
    """Return the validated dictionary view for existing public consumers."""
    return load_policy(root).document()


def detect_platform(
    os_release: Path = Path("/etc/os-release"),
    architecture: str | None = None,
    system_name: str | None = None,
) -> Platform:
    capabilities = load_policy()
    debian = capabilities.debian
    family = system_name or system_platform.system()
    arch = architecture or system_platform.machine()
    arch = capabilities.normalize_architecture(arch)
    if family != "Linux":
        return Platform(family.lower(), "unknown", arch, False, False, "UNSUPPORTED platform")
    fields: dict[str, str] = {}
    try:
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                fields[key] = value.strip().strip('"')
    except OSError:
        pass
    distro = fields.get("ID", "unknown")
    version = fields.get("VERSION_ID", "unknown")
    supported = (
        distro == "debian"
        and debian.status == CapabilityStatus.IMPLEMENTED
        and version in debian.versions
        and arch in debian.architectures
    )
    reason = (
        f"{distro.title()} {version} {arch} explicit platform adapter"
        if supported
        else "UNSUPPORTED: " + capabilities.unsupported_reason
    )
    return Platform(distro, version, arch, supported, False, reason)
