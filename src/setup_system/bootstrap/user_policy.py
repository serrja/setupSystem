"""Read the minimal trusted release inputs needed before Python dependencies exist."""

from __future__ import annotations

import platform
import re
from dataclasses import dataclass, fields
from pathlib import Path

from setup_system.resource_locator import ResourceLocator, read_toml


@dataclass(frozen=True)
class RuntimePins:
    python: str
    uv: str
    uv_base: str
    amd64_target: str
    amd64_sha256: str
    arm64_target: str
    arm64_sha256: str

    def artifact(self, arch: str) -> tuple[str, str]:
        if arch == "amd64":
            return self.amd64_target, self.amd64_sha256
        if arch == "arm64":
            return self.arm64_target, self.arm64_sha256
        raise ValueError("No reviewed runtime artifact for the target")


def runtime_pins(root: Path | None = None) -> RuntimePins:
    document = read_toml(ResourceLocator.for_root(root).manifest("runtime.toml"))
    pins_by_name = {key: value for key, value in document.items() if isinstance(value, str)}
    if pins_by_name.keys() != document.keys() or set(document) != {
        key.name for key in fields(RuntimePins)
    }:
        raise ValueError("Invalid bootstrap runtime manifest")
    pins = RuntimePins(**pins_by_name)
    if not all(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) for value in (pins.python, pins.uv)):
        raise ValueError("Invalid runtime version")
    if pins.uv_base != "https://releases.astral.sh/github/uv/releases/download":
        raise ValueError("Unexpected runtime release origin")
    for target, digest in (pins.artifact("amd64"), pins.artifact("arm64")):
        if not re.fullmatch(r"[a-z0-9_-]+", target) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid runtime target or digest")
    return pins


def _table(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError("Invalid bootstrap platform table: " + key)
    return value


def _names(document: dict[str, object], key: str) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Invalid bootstrap platform list: " + key)
    return tuple(value)


def bootstrap_architecture(root: Path | None, machine: str, *, check_host: bool = False) -> str:
    """Gate delivery before importing the application's validation library."""
    document = read_toml(ResourceLocator.for_root(root).manifest("platforms.toml"))
    debian = _table(document, "debian")
    canonical = _table(document, "architectures").get(machine, machine)
    if (
        not isinstance(canonical, str)
        or canonical not in _names(debian, "architectures")
        or debian.get("status") != "implemented"
    ):
        raise ValueError("No reviewed Debian runtime for this architecture")
    if check_host:
        release = platform.freedesktop_os_release()
        version = release.get("VERSION_ID", "")
        if (
            platform.system() != "Linux"
            or release.get("ID") != "debian"
            or version not in _names(debian, "versions")
            or release.get("VERSION_CODENAME") != _table(debian, "codenames").get(version)
        ):
            raise ValueError("Runtime delivery requires a supported Debian target")
    arch = _table(debian, "apt_architectures").get(canonical)
    if not isinstance(arch, str):
        raise ValueError("Invalid Debian architecture")
    return arch
