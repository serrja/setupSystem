"""Acquire and verify the chosen Happ release inside the private APT stage."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from setup_system.adapters.debian.package_session import system_environment
from setup_system.domain.happ import HappPin
from setup_system.infrastructure.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, copy_https


def acquire_happ(
    pin: HappPin, stage: Path, run: Callable[..., subprocess.CompletedProcess[str]]
) -> Path:
    path = stage / "happ.deb"
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        fetched = copy_https(
            pin.url, stream, DownloadPolicy(GITHUB_RELEASE_HOSTS, 256 * 1024 * 1024, 60)
        )
    if fetched.sha256 != pin.digest:
        raise ValueError("Happ artifact digest differs from the pinned release")

    metadata = run(
        ["/usr/bin/dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
        env=system_environment(),
    )
    pin.validate_metadata(metadata.stdout)
    path.chmod(0o644)
    return path
