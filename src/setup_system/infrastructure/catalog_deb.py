"""Acquire only Debian artifacts pinned in the independently verified release tree."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from setup_system.adapters.debian.packages import installed_version
from setup_system.domain.catalog import DebArtifact
from setup_system.domain.tool_releases import AcquisitionReport
from setup_system.infrastructure.catalog_repository import ARTIFACT_ORIGINS, load_components
from setup_system.infrastructure.downloads import (
    DownloadError,
    DownloadPolicy,
    copy_https,
)
from setup_system.infrastructure.system_files import write_system

ATTEMPTS = Path("/var/lib/setup-system/downloads")


def artifact(root: Path, app_id: str, arch: str) -> DebArtifact:
    if app_id not in ARTIFACT_ORIGINS or arch not in {"amd64", "arm64"}:
        raise ValueError("Unsupported package adapter or architecture")
    catalog = load_components(root, architecture=arch)
    pin = next(
        (
            entry.artifact
            for entry in catalog.entries
            if entry.id == app_id and entry.handler == "catalog_deb"
        ),
        None,
    )
    if pin is None:
        raise ValueError(f"No verified {app_id} artifact for {arch}")
    return pin


def _attempt(app_id: str, arch: str, version: str, result: str) -> None:
    report = AcquisitionReport.model_validate(
        {
            "app": app_id,
            "version": version,
            "architecture": arch,
            "result": result,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    # The published system receipt identifies the app by filename.
    content = report.model_dump_json(exclude={"app"})
    write_system(ATTEMPTS / f"{app_id}.json", (content + "\n").encode())
    print("setup-system/download " + report.model_dump_json(), flush=True)


def stage_deb(root: Path, app_id: str, arch: str, stage: Path) -> Path:
    pin = artifact(root, app_id, arch)
    path = stage / f"{app_id}_{pin.version}_{arch}.deb"
    try:
        with path.open("xb") as target:
            download = copy_https(
                pin.url, target, DownloadPolicy(ARTIFACT_ORIGINS[app_id], 650_000_000, 60)
            )
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

    from setup_system.infrastructure.config_store import SettingsPaths
    from setup_system.infrastructure.user_storage import atomic_write, safe_file

    for line in (output or "").splitlines():
        if not line.startswith("setup-system/download ") or len(line) > 1000:
            continue
        report = AcquisitionReport.model_validate_json(line.removeprefix("setup-system/download "))
        path = SettingsPaths().state / "downloads" / f"{report.app}.json"
        safe_file(path, os.getuid())
        atomic_write(path, (report.model_dump_json() + "\n").encode(), 0o600)


def last_attempt(app_id: str) -> str | None:
    import os

    from setup_system.infrastructure.config_store import SettingsPaths
    from setup_system.infrastructure.user_storage import safe_file

    if app_id not in ARTIFACT_ORIGINS:
        return None
    path = SettingsPaths().state / "downloads" / f"{app_id}.json"
    try:
        safe_file(path, os.getuid())
        if not path.exists():
            return None
        if path.stat().st_size > 4096:
            raise ValueError("Unexpected package acquisition record")
        report = AcquisitionReport.model_validate_json(path.read_bytes())
        if report.app != app_id:
            raise ValueError("Package acquisition record belongs to a different app")
        return f"{report.time} · {report.version} ({report.architecture}) · {report.result}"
    except (OSError, ValueError):
        return "Saved acquisition record requires review."
