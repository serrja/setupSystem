"""Acquire only Debian artifacts pinned in the independently verified release tree."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from setup_system.adapters.debian.packages import installed_version
from setup_system.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadError,
    DownloadPolicy,
    copy_https,
    validate_https_url,
)
from setup_system.installation import _write_system

ORIGINS = {
    "chatgpt": frozenset({"persistent.oaistatic.com"}),
    "obsidian": GITHUB_RELEASE_HOSTS,
}
ATTEMPTS = Path("/var/lib/setup-system/downloads")


@dataclass(frozen=True)
class Artifact:
    version: str
    url: str
    sha256: str


def artifact(root: Path, app_id: str, arch: str) -> Artifact:
    catalog = tomllib.loads((root / "manifests/applications.toml").read_text())
    if app_id not in ORIGINS or arch not in {"amd64", "arm64"}:
        raise ValueError("Unsupported package adapter or architecture")
    row = catalog[app_id].get("artifacts", {}).get(arch)
    if not row or catalog[app_id]["handler"] != "catalog_deb":
        raise ValueError(f"No verified {app_id} artifact for {arch}")
    pin = Artifact(**row)
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~-]{0,100}", pin.version) or not re.fullmatch(
        r"[a-f0-9]{64}", pin.sha256
    ):
        raise ValueError("Invalid release artifact pin")
    validate_https_url(pin.url, ORIGINS[app_id])
    return pin


def _attempt(app_id: str, arch: str, version: str, result: str) -> None:
    content = {
        "version": version,
        "architecture": arch,
        "result": result,
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _write_system(ATTEMPTS / f"{app_id}.json", (json.dumps(content) + "\n").encode())
    print("setup-system/download " + json.dumps({**content, "app": app_id}), flush=True)


def stage_deb(root: Path, app_id: str, arch: str, stage: Path) -> Path:
    pin = artifact(root, app_id, arch)
    path = stage / f"{app_id}_{pin.version}_{arch}.deb"
    try:
        with path.open("xb") as target:
            download = copy_https(pin.url, target, DownloadPolicy(ORIGINS[app_id], 650_000_000, 60))
        if download.sha256 != pin.sha256:
            raise ValueError("Artifact checksum mismatch")
        result = subprocess.run(
            ["/usr/bin/dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        if result.stdout.splitlines() != [
            "Package: " + app_id,
            "Version: " + pin.version,
            "Architecture: " + arch,
        ]:
            raise ValueError("Package identity differs from its reviewed artifact pin")
    except (ValueError, DownloadError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, (DownloadError, ValueError)) else type(exc).__name__
        _attempt(app_id, arch, pin.version, reason)
        raise
    path.chmod(0o644)
    _attempt(app_id, arch, pin.version, "verified")
    return path


def verify_installed(root: Path, app_id: str, arch: str) -> None:
    if installed_version(app_id + ":" + arch) != artifact(root, app_id, arch).version:
        raise RuntimeError(f"Installed {app_id} version did not match the confirmed package")


def record_reported_attempts(output: str | None) -> None:
    """Keep the current user's bounded copy of the root acquisition result."""
    import os

    from setup_system.infrastructure.config_store import SettingsPaths, safe_file
    from setup_system.storage import atomic_write

    for line in (output or "").splitlines():
        if not line.startswith("setup-system/download ") or len(line) > 1000:
            continue
        row = json.loads(line.removeprefix("setup-system/download "))
        app = row.get("app")
        if (
            app not in ORIGINS
            or set(row) != {"app", "version", "time", "architecture", "result"}
            or not all(isinstance(v, str) for v in row.values())
        ):
            raise ValueError("Invalid package acquisition result")
        path = SettingsPaths().state / "downloads" / f"{app}.json"
        safe_file(path, os.getuid())
        atomic_write(path, (json.dumps(row) + "\n").encode(), 0o600)


def last_attempt(app_id: str) -> str | None:
    import os

    from setup_system.infrastructure.config_store import SettingsPaths, safe_file

    if app_id not in ORIGINS:
        return None
    path = SettingsPaths().state / "downloads" / f"{app_id}.json"
    try:
        safe_file(path, os.getuid())
        if not path.exists():
            return None
        if path.stat().st_size > 4096:
            raise ValueError("Unexpected package acquisition record")
        row = json.loads(path.read_bytes())
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str) for key in ("time", "version", "architecture", "result")
        ):
            raise ValueError("Invalid package acquisition record")
        return f"{row['time']} · {row['version']} ({row['architecture']}) · {row['result']}"
    except (OSError, ValueError):
        return "Saved acquisition record requires review."
