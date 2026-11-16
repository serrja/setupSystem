"""Read-only package metadata, independent of a graphical session."""

from __future__ import annotations

import re
import subprocess


def installed_version(package: str) -> str | None:
    """Inspect a fully installed Debian package without executing its binaries."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+(?::[a-z0-9]+)?", package):
        raise ValueError("Invalid Debian package name")
    result = subprocess.run(
        ["/usr/bin/dpkg-query", "--show", "--showformat=${Status}\t${Version}\n", package],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode == 1:
        return None
    result.check_returncode()
    status, separator, version = result.stdout.strip().partition("\t")
    if status != "install ok installed":
        return None
    if not separator or not re.fullmatch(r"[A-Za-z0-9.+:~_-]+", version):
        raise ValueError("Invalid installed Debian package metadata")
    return version
