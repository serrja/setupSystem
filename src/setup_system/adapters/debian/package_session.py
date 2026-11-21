"""Typed Debian package selections and progress, without privileged side effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

DEBIAN_ARCHIVE = "https://deb.debian.org/debian"
DEBIAN_SECURITY = "https://security.debian.org/debian-security"
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
    selection: tuple[str, ...]

    def options(self) -> list[str]:
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
            "Acquire::http::Proxy=DIRECT",
            "Acquire::https::Proxy=DIRECT",
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
            *self.options(),
            "--yes",
            "--no-download",
            *self.install_arguments(),
        ]
