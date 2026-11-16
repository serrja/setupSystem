"""Explicit synthetic package inputs; no fixture installs software on the host."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from setup_system import installation


@pytest.fixture
def package_fixture(
    tmp_path: Path,
) -> tuple[Path, installation.TargetUser, Path, installation.SystemPaths]:
    root = tmp_path / "verified source"
    (root / "manifests").mkdir(parents=True)
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "manifests/platforms.toml",
        root / "manifests/platforms.toml",
    )
    home = tmp_path / "desktop home"
    home.mkdir()
    happ = home / "Happ fixture.deb"
    happ.write_bytes(b"Synthetic Happ archive; metadata comes from the mock package executor.\n")
    digest = hashlib.sha256(happ.read_bytes()).hexdigest()
    (root / "manifests/happ.toml").write_text(
        'version = "4.5.4"\npackageVersion = "4.5.4-333"\n'
        '[linux.amd64]\npackageArchitecture = "amd64"\n'
        f'sha256 = "{digest}"\n'
        '[linux.arm64]\npackageArchitecture = "arm64"\n'
        f'sha256 = "{digest}"\n'
    )
    target = installation.TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/bash")
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    keyring = tmp_path / "debian-archive-keyring.pgp"
    keyring.write_bytes(b"Synthetic Debian trust anchor")
    paths = installation.SystemPaths(
        tmp_path / "etc/setup-system-backports.sources",
        tmp_path / "state/install.json",
        keyring,
        runtime,
        tmp_path / "policy-rc.d",
        happ_unit=tmp_path / "etc-systemd/happd.service",
        happ_runtime_unit=tmp_path / "run-systemd/happd.service",
    )
    return root, target, happ, paths
