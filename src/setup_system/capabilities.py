"""Validated, immutable platform capabilities shared by read-only and system adapters."""

from __future__ import annotations

import copy
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType


class CapabilityStatus(StrEnum):
    IMPLEMENTED = "implemented"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class DebianCapabilities:
    versions: tuple[str, ...]
    architectures: tuple[str, ...]
    codenames: Mapping[str, str]
    apt_architectures: Mapping[str, str]
    status: CapabilityStatus
    locally_verified: bool
    terminal_dev_arm64: str


@dataclass(frozen=True)
class HyprlandCapabilities:
    distribution: str
    version: str
    codename: str
    apt_architectures: tuple[str, ...]
    architectures: tuple[str, ...]
    version_pin: str
    upstream_version: str
    backports: str
    init_system: str
    init_version: str
    preflight_paths: tuple[str, ...]
    commands: tuple[str, ...]
    base_packages: tuple[str, ...]
    backport_packages: Mapping[str, str]


@dataclass(frozen=True)
class CapabilityPolicy:
    aliases: Mapping[str, str]
    debian: DebianCapabilities
    hyprland: HyprlandCapabilities
    unsupported_reason: str
    _document: Mapping[str, object] = field(repr=False, compare=False)

    def normalize_architecture(self, architecture: str) -> str:
        """Keep unknown machines available to a caller's explicit unsupported gate."""
        return self.aliases.get(architecture, architecture)

    def debian_architecture(self, architecture: str) -> str:
        machine = self.normalize_architecture(architecture)
        try:
            return self.debian.apt_architectures[machine]
        except KeyError:
            raise ValueError("No exact Debian package architecture matches this target") from None

    def debian_codename(self, version: str) -> str:
        try:
            return self.debian.codenames[version]
        except KeyError:
            raise ValueError("No Debian codename matches this version") from None

    def document(self) -> dict[str, object]:
        """Provide a detached compatibility view without weakening the typed policy."""
        return copy.deepcopy(dict(self._document))


def _mapping(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or not value or any(not isinstance(k, str) for k in value):
        raise ValueError(f"Invalid platform capability mapping: {location}")
    return {key: item for key, item in value.items()}


def _text(value: object, location: str, pattern: str | None = None) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or any(ord(character) < 32 for character in value)
        or (pattern is not None and re.fullmatch(pattern, value) is None)
    ):
        raise ValueError(f"Invalid platform capability value: {location}")
    return value


def _strings(value: object, location: str, pattern: str | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Invalid platform capability list: {location}")
    result = tuple(_text(item, location, pattern) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"Repeated platform capability list entry: {location}")
    return result


def _strings_map(
    value: object, location: str, key_pattern: str, value_pattern: str
) -> Mapping[str, str]:
    raw = _mapping(value, location)
    result = {
        _text(key, location, key_pattern): _text(item, location, value_pattern)
        for key, item in raw.items()
    }
    return MappingProxyType(result)


def parse_policy(document: Mapping[str, object]) -> CapabilityPolicy:
    """Validate all consumers' fields before any package or filesystem operation."""
    raw = _mapping(document, "root")
    architecture_name = r"[a-z0-9][a-z0-9_]*"
    package_name = r"[a-z0-9][a-z0-9+.-]*"
    package_version = r"[A-Za-z0-9.+:~_-]+"
    release_name = r"[a-z][a-z0-9-]*"
    aliases = _strings_map(
        raw.get("architectures"), "architectures", architecture_name, architecture_name
    )
    debian_raw = _mapping(raw.get("debian"), "debian")
    versions = _strings(debian_raw.get("versions"), "debian.versions", r"[0-9]+")
    architectures = _strings(
        debian_raw.get("architectures"), "debian.architectures", architecture_name
    )
    codenames = _strings_map(
        debian_raw.get("codenames"), "debian.codenames", r"[0-9]+", release_name
    )
    apt_architectures = _strings_map(
        debian_raw.get("apt_architectures"),
        "debian.apt_architectures",
        architecture_name,
        architecture_name,
    )
    if any(version not in codenames for version in versions):
        raise ValueError("Missing Debian version codename in capability manifest")
    if set(apt_architectures) != set(architectures) or len(set(apt_architectures.values())) != len(
        architectures
    ):
        raise ValueError("Debian architecture mapping does not match the supported architectures")
    if any(key in architectures or value not in architectures for key, value in aliases.items()):
        raise ValueError("Architecture aliases must resolve directly to a supported architecture")
    if any(aliases.get(value, value) != key for key, value in apt_architectures.items()):
        raise ValueError("Debian package architectures and aliases disagree")
    verified = debian_raw.get("locallyVerified")
    if not isinstance(verified, bool):
        raise ValueError("Invalid Debian capability verification state")
    status = _text(debian_raw.get("status"), "debian.status")
    try:
        capability_status = CapabilityStatus(status)
    except ValueError:
        raise ValueError("Invalid Debian capability status") from None
    debian = DebianCapabilities(
        versions,
        architectures,
        codenames,
        apt_architectures,
        capability_status,
        verified,
        _text(debian_raw.get("terminalDevArm64"), "debian.terminalDevArm64"),
    )
    profile_raw = _mapping(raw.get("hyprland"), "hyprland")
    profile_architectures = _strings(
        profile_raw.get("architectures"), "hyprland.architectures", architecture_name
    )
    profile_apt = _strings(
        profile_raw.get("apt_architectures"), "hyprland.apt_architectures", architecture_name
    )
    if any(
        machine not in architectures for machine in profile_architectures
    ) or profile_apt != tuple(apt_architectures[machine] for machine in profile_architectures):
        raise ValueError("Hyprland architectures disagree with the Debian capability mapping")
    profile_version = _text(profile_raw.get("version"), "hyprland.version", r"[0-9]+")
    codename = _text(profile_raw.get("codename"), "hyprland.codename", release_name)
    if profile_version not in versions or codenames.get(profile_version) != codename:
        raise ValueError("Hyprland version and Debian codename disagree")
    if (
        profile_raw.get("distribution") != "debian"
        or profile_raw.get("backports") != codename + "-backports"
    ):
        raise ValueError("Hyprland requires the matching official Debian backports capability")
    preflight_paths = _strings(profile_raw.get("preflight_paths"), "hyprland.preflight_paths")
    for specification in preflight_paths:
        kind, separator, relative = specification.partition(":")
        path = PurePosixPath(relative)
        if (
            kind not in {"config", "data", "state", "cache"}
            or not separator
            or not relative
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.rstrip("/").split("/"))
            or "\\" in relative
        ):
            raise ValueError("Invalid managed destination in capability manifest")
    profile = HyprlandCapabilities(
        _text(profile_raw.get("distribution"), "hyprland.distribution", release_name),
        profile_version,
        codename,
        profile_apt,
        profile_architectures,
        _text(profile_raw.get("version_pin"), "hyprland.version_pin", package_version),
        _text(
            profile_raw.get("upstream_version"),
            "hyprland.upstream_version",
            r"[0-9]+(?:\.[0-9]+){2}",
        ),
        _text(profile_raw.get("backports"), "hyprland.backports", release_name),
        _text(profile_raw.get("init_system"), "hyprland.init_system", release_name),
        _text(profile_raw.get("init_version"), "hyprland.init_version", r"[0-9]+(?:\.[0-9]+)*"),
        preflight_paths,
        _strings(profile_raw.get("commands"), "hyprland.commands", r"[A-Za-z0-9][A-Za-z0-9+._-]*"),
        _strings(profile_raw.get("base_packages"), "hyprland.base_packages", package_name),
        _strings_map(
            profile_raw.get("backport_packages"),
            "hyprland.backport_packages",
            package_name,
            package_version,
        ),
    )
    unsupported = _mapping(raw.get("unsupported"), "unsupported")
    return CapabilityPolicy(
        aliases,
        debian,
        profile,
        _text(unsupported.get("reason"), "unsupported.reason"),
        MappingProxyType(copy.deepcopy(raw)),
    )


def load_policy(root: Path | None = None) -> CapabilityPolicy:
    if root is None:
        source = Path(__file__).resolve().parents[2]
        root = source if (source / "manifests/platforms.toml").is_file() else Path(__file__).parent
    with (root / "manifests/platforms.toml").open("rb") as stream:
        return parse_policy(tomllib.load(stream))
