"""Typed Debian package selections and progress, without privileged side effects."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from setup_system.installation_state import (
    InstallJournal as InstallJournal,
)
from setup_system.installation_state import (
    InstallStage as InstallStage,
)
from setup_system.installation_state import (
    InstallStatus as InstallStatus,
)

DEBIAN_ARCHIVE = "http://deb.debian.org/debian"
DEBIAN_SECURITY = "http://security.debian.org/debian-security"
APT_TIMEOUT = 180
PACKAGE_TIMEOUT = 1800
SERVICE_QUERY_TIMEOUT = 30
SERVICE_START_TIMEOUT = 60


def system_environment(*, package_manager: bool = False) -> dict[str, str]:
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    if package_manager:
        environment["DEBIAN_FRONTEND"] = "noninteractive"
    return environment


@dataclass(frozen=True)
class AptSources:
    codename: str
    backports: str
    keyring: Path

    def _entry(self, suites: str, *, security: bool = False) -> str:
        archive = DEBIAN_SECURITY if security else DEBIAN_ARCHIVE
        return (
            f"Types: deb\nURIs: {archive}\nSuites: {suites}\n"
            f"Components: main\nSigned-By: {self.keyring}\n"
        )

    def managed_backports(self) -> bytes:
        return (
            "# Managed by setupSystem; official Debian backports only.\n"
            + self._entry(self.backports)
        ).encode()

    def staging(self) -> str:
        return (
            self._entry(f"{self.codename} {self.codename}-updates {self.backports}")
            + "\n"
            + self._entry(f"{self.codename}-security", security=True)
        )


@dataclass(frozen=True)
class AptCommands:
    stage: Path
    proxy: str | None
    selection: tuple[str, ...]

    def options(self, *, offline: bool = False) -> list[str]:
        settings = (
            f"Dir::Etc::sourcelist={self.stage / 'debian.sources'}",
            "Dir::Etc::sourceparts=-",
            f"Dir::State::lists={self.stage / 'lists'}",
            f"Dir::Cache::archives={self.stage / 'archives'}",
            "Acquire::AllowInsecureRepositories=false",
            "APT::Get::AllowUnauthenticated=false",
            "APT::Update::Error-Mode=any",
            "Acquire::Retries=0",
            "Acquire::http::Timeout=30",
            "Acquire::https::Timeout=30",
            "DPkg::Lock::Timeout=30",
            f"Acquire::http::Proxy={'DIRECT' if offline else self.proxy or 'DIRECT'}",
            f"Acquire::https::Proxy={'DIRECT' if offline else self.proxy or 'DIRECT'}",
        )
        return [argument for setting in settings for argument in ("-o", setting)]

    def install_arguments(self) -> list[str]:
        return ["--no-remove", "--no-install-recommends", "install", *self.selection]

    def update(self) -> list[str]:
        return ["/usr/bin/apt-get", *self.options(), "update"]

    def simulate(self) -> list[str]:
        return ["/usr/bin/apt-get", *self.options(), "--simulate", *self.install_arguments()]

    def freeze(self, selection: list[str]) -> AptCommands:
        return replace(self, selection=tuple(selection))

    def download(self) -> list[str]:
        return [
            "/usr/bin/apt-get",
            *self.options(),
            "--download-only",
            "--yes",
            *self.install_arguments(),
        ]

    def apply(self) -> list[str]:
        return [
            "/usr/bin/unshare",
            "--net",
            "--",
            "/usr/bin/apt-get",
            *self.options(offline=True),
            "--yes",
            "--no-download",
            *self.install_arguments(),
        ]


@dataclass(frozen=True)
class HappPin:
    digest: str
    version: str
    architecture: str

    @classmethod
    def load(cls, path: Path, architecture: str) -> HappPin:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError("Happ manifest must be a bounded regular file")
        manifest = tomllib.loads(path.read_text())
        linux = manifest.get("linux")
        asset = linux.get(architecture) if isinstance(linux, dict) else None
        if not isinstance(asset, dict):
            raise ValueError("Missing pinned Happ package for the target architecture")
        digest, version = asset.get("sha256"), manifest.get("packageVersion")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(version, str)
            or re.fullmatch(r"[A-Za-z0-9.+:~_-]{1,200}", version) is None
            or asset.get("packageArchitecture") != architecture
        ):
            raise ValueError("Invalid pinned Happ digest, version or target architecture")
        return cls(digest, version, architecture)

    def validate_metadata(self, output: str) -> None:
        fields: dict[str, str] = {}
        for line in output.splitlines():
            name, separator, value = line.partition(":")
            if not separator or name in fields:
                raise ValueError("Happ archive metadata differs from the pinned release")
            fields[name] = value.strip()
        if fields != {
            "Package": "happ",
            "Version": self.version,
            "Architecture": self.architecture,
        }:
            raise ValueError("Happ archive metadata differs from the pinned release")

    def cache_name(self) -> str:
        return f"happ_{self.version}_{self.architecture}.deb"
