"""Check system package destinations and the private offline APT namespace."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from setup_system.adapters.debian.package_session import system_environment
from setup_system.adapters.debian.service_policy import check_service_policy
from setup_system.infrastructure.system_files import validate_system_path


@dataclass(frozen=True)
class SystemPaths:
    backports: Path = Path("/etc/apt/sources.list.d/setup-system-backports.sources")
    journal: Path = Path("/var/lib/setup-system/install.json")
    keyring: Path = Path("/usr/share/keyrings/debian-archive-keyring.pgp")
    runtime: Path = Path("/run")
    policy: Path = Path("/usr/sbin/policy-rc.d")
    happ_unit: Path = Path("/etc/systemd/system/happd.service")
    happ_runtime_unit: Path = Path("/run/systemd/system/happd.service")


def validate_package_paths(
    paths: SystemPaths, backport_source: bytes, *, persist_backports: bool
) -> None:
    if paths.keyring.is_symlink() or not paths.keyring.is_file():
        raise ValueError(f"Installed regular Debian archive keyring is required: {paths.keyring}")
    if persist_backports:
        validate_system_path(paths.backports)
        if paths.backports.exists() and paths.backports.read_bytes() != backport_source:
            raise ValueError("Existing backports file differs; review it without overwriting")
    check_service_policy(paths.policy)
    validate_system_path(paths.journal)
    if (
        not paths.runtime.is_absolute()
        or any(parent.is_symlink() for parent in (paths.runtime, *paths.runtime.parents))
        or not paths.runtime.is_dir()
    ):
        raise ValueError("APT staging needs a regular trusted runtime directory")
    details = paths.runtime.stat()
    if details.st_uid != os.geteuid() or details.st_mode & 0o022:
        raise ValueError("APT staging directory is not privately controlled")


def check_offline_namespace(run: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    command = ["/usr/bin/unshare", "--net", "--", "/usr/bin/true"]
    try:
        result = run(
            command,
            env=system_environment(package_manager=True),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, command, result.stdout, result.stderr
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "Offline package apply requires root network namespace support (unshare --net)"
        ) from exc
