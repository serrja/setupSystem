"""Read-only application inventory and narrow Debian installation actions."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from setup_system.planning import detect_platform


class Application(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    purpose: str
    publisher: str
    source: str
    method: str
    detect: str
    package: str
    privilege: str
    data: str
    supported_debian_versions: list[str] | None = Field(
        default=None, alias="supportedDebianVersions"
    )


def catalog(root: Path) -> dict[str, Application]:
    raw = tomllib.loads((root / "manifests/applications.toml").read_text())
    return {name: Application.model_validate(entry) for name, entry in raw.items()}


def _deb_status(package: str) -> str:
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status} ${Version}", package],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode == 0 and result.stdout.startswith("install ok installed "):
        return "deb " + result.stdout.removeprefix("install ok installed ")
    return "absent"


def app_status(app: Application) -> str:
    package_status = _deb_status(app.package)
    binary = shutil.which(app.detect)
    if package_status.startswith("deb "):
        return package_status
    if binary:
        return f"present ({binary}; package source not established)"
    if package_status == "unknown":
        return "unknown"
    return "absent"


def install_zsh(app: Application) -> str:
    platform = detect_platform()
    if not platform.implemented or platform.distribution != "debian":
        raise ValueError(platform.reason)
    if app.package != "zsh":
        raise ValueError("This installer action is only for Debian zsh")
    existing = app_status(app)
    if existing != "absent":
        return "unchanged: " + existing
    if not shutil.which("sudo"):
        raise RuntimeError("sudo is unavailable; install Debian zsh with an administrator")
    result = subprocess.run(
        ["sudo", "-n", "apt-get", "install", "-y", "zsh"], timeout=300, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"Debian apt installation failed with exit code {result.returncode}")
    verified = app_status(app)
    if not verified.startswith("deb "):
        raise RuntimeError("zsh was not detected after apt installation")
    return "changed: " + verified
