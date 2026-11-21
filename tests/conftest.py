"""Explicit synthetic package inputs; no fixture installs software on the host."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from setup_system.adapters.debian import apt_paths
from setup_system.domain import identity as account_identity


@pytest.fixture
def package_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, account_identity.TargetUser, Path, apt_paths.SystemPaths]:
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
    import re
    from types import SimpleNamespace

    from setup_system.adapters.debian import happ_artifact

    manifest = (Path(__file__).resolve().parents[1] / "manifests/happ.toml").read_text()
    manifest = re.sub(r'(?m)^sha256 = ".*"$', 'sha256 = "' + digest + '"', manifest)
    (root / "manifests/happ.toml").write_text(manifest)

    def fetch(url, output, policy):
        assert url.startswith("https://github.com/Happ-proxy/happ-desktop/releases/download/")
        data = happ.read_bytes()
        output.write(data)
        return SimpleNamespace(sha256=hashlib.sha256(data).hexdigest())

    monkeypatch.setattr(happ_artifact, "copy_https", fetch)
    target = account_identity.TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/bash")
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    keyring = tmp_path / "debian-archive-keyring.pgp"
    keyring.write_bytes(b"Synthetic Debian trust anchor")
    paths = apt_paths.SystemPaths(
        tmp_path / "etc/setup-system-backports.sources",
        tmp_path / "state/install.json",
        keyring,
        runtime,
        tmp_path / "policy-rc.d",
        happ_unit=tmp_path / "etc-systemd/happd.service",
        happ_runtime_unit=tmp_path / "run-systemd/happd.service",
    )
    return root, target, happ, paths
