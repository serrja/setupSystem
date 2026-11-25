"""Verify signed release metadata and real Debian package headers in a private stage."""

import hashlib
import json
import subprocess

import pytest
from setup_plugin_happ import Plugin

from setup_system_sdk import apt


def archive(tmp_path, name, version, arch):
    package = tmp_path / "fixture"
    (package / "DEBIAN").mkdir(parents=True)
    (package / "DEBIAN").chmod(0o755)
    control = (
        f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
        "Maintainer: Test <test@invalid.tld>\nDescription: fixture\n"
    )
    (package / "DEBIAN/control").write_text(control)
    result = tmp_path / "vendor.deb"
    subprocess.run(
        ["/usr/bin/dpkg-deb", "--root-owner-group", "--build", str(package), str(result)],
        check=True,
        capture_output=True,
    )
    return result


@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "draft",
        "prerelease",
        "duplicate",
        "digest",
        "redirect",
        "missing",
        "unsupported",
        "bad-tag",
        "malformed",
        "other-arch",
        "other-package",
    ],
)
def test_official_stable_asset_and_native_deb(tmp_path, monkeypatch, change):
    stage = tmp_path / "stage"
    stage.mkdir()
    policy = apt.Packages.model_validate_json((Plugin.package_folder / "apt.json").read_bytes())
    arch = "arm64" if change == "other-arch" else "amd64"
    deb = archive(
        tmp_path, "other-package" if change == "other-package" else "happ", "1.2-123", arch
    ).read_bytes()
    name = policy.release.asset["amd64"]
    url = "https://github.com/" + policy.release.github + "/releases/download/1.2/" + name
    asset = {
        "name": name,
        "browser_download_url": url,
        "digest": "sha256:" + hashlib.sha256(deb).hexdigest(),
    }
    info = {
        "draft": False,
        "prerelease": False,
        "tag_name": "1.2",
        "published_at": "2026-01-01",
        "assets": [asset],
    }
    if change in {"draft", "prerelease"}:
        info[change] = True
    elif change == "duplicate":
        info["assets"].append(asset)
    elif change == "digest":
        asset["digest"] = "sha256:" + "0" * 64
    elif change == "redirect":
        asset["browser_download_url"] = "https://example.invalid/fixture.deb"
    elif change == "missing":
        info["assets"] = []
    elif change == "bad-tag":
        info["tag_name"] = "nightly"
    elif change == "malformed":
        asset["digest"] = None
    monkeypatch.setattr(
        apt, "current_target", lambda: ("i386" if change == "unsupported" else "amd64", True)
    )
    monkeypatch.setattr(
        apt,
        "read_https",
        lambda address, policy: json.dumps(info).encode() if "api.github.com" in address else deb,
    )
    if change == "valid":
        path, digest = apt.archive(stage, policy)
        assert path.read_bytes() == deb and digest == hashlib.sha256(deb).hexdigest()
    else:
        with pytest.raises(ValueError):
            apt.archive(stage, policy)
