"""Read-only planning shared by application services and Textual."""

from __future__ import annotations

import platform as system_platform
from pathlib import Path

from setup_system.domain.platforms import CapabilityStatus
from setup_system.domain.system_facts import Platform
from setup_system.infrastructure.catalog_repository import load_policy


def detect_platform(
    os_release: Path = Path("/etc/os-release"),
    architecture: str | None = None,
    system_name: str | None = None,
    root: Path | None = None,
) -> Platform:
    capabilities = load_policy(root)
    debian = capabilities.debian
    family = system_name or system_platform.system()
    arch = architecture or system_platform.machine()
    arch = capabilities.normalize_architecture(arch)
    if family != "Linux":
        return Platform(
            distribution=family.lower(),
            version="unknown",
            architecture=arch,
            implemented=False,
            verified=False,
            reason="UNSUPPORTED platform",
        )
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
    return Platform(
        distribution=distro,
        version=version,
        architecture=arch,
        implemented=supported,
        verified=False,
        reason=reason,
    )
