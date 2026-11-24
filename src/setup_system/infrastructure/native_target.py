"""Match the supported Debian distribution and kernel architecture before any system write."""

import platform
import subprocess
from collections.abc import Mapping


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
            dpkg_arch = subprocess.check_output(
                ["/usr/bin/dpkg", "--print-architecture"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return target(facts, machine, dpkg_arch)
