"""Validate the selected vendor package and its reviewed systemd service."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from setup_system.adapters.debian.apt_paths import SystemPaths
from setup_system.adapters.debian.package_session import (
    SERVICE_QUERY_TIMEOUT,
    SERVICE_START_TIMEOUT,
    system_environment,
)
from setup_system.infrastructure.catalog_repository import load_policy
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.system_files import validate_system_path


def validate_service_manager(
    *,
    root: Path | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> None:
    """Require the matrix's reviewed manager API without changing units or PID 1."""
    profile = load_policy(root).hyprland
    expected = profile.init_version
    if profile.init_system != "systemd":
        raise ValueError("Happ service controls require systemd")
    environment = system_environment()
    try:
        client = run(
            ["/usr/bin/systemctl", "--version"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if (
            client.returncode
            or re.match(r"systemd " + re.escape(expected) + r"(?:[ .(]|$)", client.stdout) is None
        ):
            raise ValueError(f"Happ requires the reviewed Debian systemd {expected} client")
        manager = run(
            [
                "/usr/bin/systemctl",
                "--system",
                "--no-pager",
                "--no-ask-password",
                "show",
                "--property=Version",
                "--value",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if (
            manager.returncode
            or re.match(re.escape(expected) + r"(?:[.~-]|$)", manager.stdout.strip()) is None
        ):
            raise ValueError(
                f"Happ requires an accessible systemd {expected} system manager as init"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            f"Happ requires systemctl and an accessible systemd {expected} manager"
        ) from exc


def check_happ_unit_policy(paths: SystemPaths) -> None:
    for path in (paths.happ_unit, paths.happ_runtime_unit):
        if path.is_symlink():
            raise ValueError(
                "Happ service is masked or redirected by an administrator; "
                "review it without overwriting"
            )
        if path.exists() and not path.is_file():
            raise ValueError(
                "Existing Happ service unit is not a regular administrator-controlled file"
            )
        validate_system_path(path)


def check_existing_happ(
    expected_version: str,
    *,
    expected_happ_version: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Refuse implicit Happ upgrades or repairs that could disrupt an active connection."""
    message = (
        "Happ package state requires manual review before installation; "
        "only an absent package or the exact configured pinned release is supported"
    )
    environment = system_environment()
    try:
        result = run(
            [
                "/usr/bin/dpkg-query",
                "--no-pager",
                "--show",
                r"--showformat=${Status}\n${Version}\n",
                "happ",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError(message) from None
    if expected_happ_version is not None:
        if not re.fullmatch(r"[A-Za-z0-9.+:~_-]{1,200}", expected_happ_version):
            raise ValueError(message)
        if result.returncode or result.stdout != f"install ok installed\n{expected_happ_version}\n":
            raise ValueError("Happ package changed after confirming its version")
        if expected_happ_version != expected_version:
            active = run(
                ["/usr/bin/systemctl", "--quiet", "is-active", "happd.service"],
                check=False,
                capture_output=True,
                timeout=15,
                env=environment,
            )
            if active.returncode not in {3, 4}:
                raise ValueError("Disconnect and stop the active Happ service before upgrading")
        return
    if result.returncode == 1 and result.stdout == "":
        return
    if result.returncode == 0 and result.stdout == f"install ok installed\n{expected_version}\n":
        return
    raise ValueError(message)


def activate_service(
    run: Callable[..., subprocess.CompletedProcess[str]],
    execute: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    command = ["/usr/bin/systemctl", "--system", "--no-pager", "--no-ask-password"]
    try:
        execute([*command, "daemon-reload"], timeout=SERVICE_QUERY_TIMEOUT)
        current = run(
            [*command, "--quiet", "is-active", "happd.service"],
            env=system_environment(package_manager=True),
            text=True,
            capture_output=True,
            check=False,
            timeout=SERVICE_QUERY_TIMEOUT,
        )
        if current.returncode not in {0, 3, 4}:
            raise RuntimeError("Cannot inspect happd.service activation state")
        if current.returncode:
            execute([*command, "start", "happd.service"], timeout=SERVICE_START_TIMEOUT)
        execute([*command, "--quiet", "is-active", "happd.service"], timeout=SERVICE_QUERY_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Vendor happd.service could not be activated; review its system journal"
        ) from exc
