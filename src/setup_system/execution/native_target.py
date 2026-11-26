"""Match the supported Debian distribution and kernel architecture before any system write."""

import hashlib
import platform
import subprocess
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from setup_system.execution.processes import run_command


def architecture(machine: str) -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)


def target(release: Mapping[str, str], machine: str, dpkg_arch: str | None) -> tuple[str, bool]:
    arch = architecture(machine)
    supported = (
        release.get("ID") == "debian"
        and release.get("VERSION_ID") == "13"
        and release.get("VERSION_CODENAME") == "trixie"
        and arch in {"amd64", "arm64"}
        and dpkg_arch == arch
    )
    return arch, supported


def get_release() -> dict[str, str]:
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {}


def current_target(release: Mapping[str, str] | None = None) -> tuple[str, bool]:
    facts = get_release() if release is None else release
    machine = platform.machine()
    dpkg_arch = None
    if facts.get("ID") == "debian":
        try:
            dpkg_arch = run_command(
                ["/usr/bin/dpkg", "--print-architecture"],
                discard_stderr=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return target(facts, machine, dpkg_arch)


def current_boot_identity() -> str:
    identity = UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    return hashlib.sha256(b"setup-system:admin:v1\x00" + identity.bytes).hexdigest()
