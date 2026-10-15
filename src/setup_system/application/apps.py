"""Read-only application inventory and narrow Ubuntu installation actions."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict


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


def catalog(root: Path) -> dict[str, Application]:
    raw = tomllib.loads((root / "manifests/applications.toml").read_text())
    return {name: Application.model_validate(entry) for name, entry in raw.items()}


def _debStatus(package: str) -> str:
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


def appStatus(app: Application) -> str:
    packageStatus = _debStatus(app.package)
    binary = shutil.which(app.detect)
    if packageStatus.startswith("deb "):
        return packageStatus
    if binary:
        return f"present ({binary}; package source not established)"
    if packageStatus == "unknown":
        return "unknown"
    return "absent"


def installZsh(app: Application) -> str:
    if app.package != "zsh":
        raise ValueError("This installer action is only for Ubuntu zsh")
    existing = appStatus(app)
    if existing != "absent":
        return "unchanged: " + existing
    if not shutil.which("sudo"):
        raise RuntimeError("sudo is unavailable; install Ubuntu zsh with an administrator")
    result = subprocess.run(
        ["sudo", "-n", "apt-get", "install", "-y", "zsh"], timeout=300, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"Ubuntu apt installation failed with exit code {result.returncode}")
    verified = appStatus(app)
    if not verified.startswith("deb "):
        raise RuntimeError("zsh was not detected after apt installation")
    return "changed: " + verified
