"""Pure package/session policy stays typed without executing system commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from setup_system.adapters.debian.package_session import (
    AptCommands,
    AptSources,
)
from setup_system.domain.happ import HappPin
from setup_system.infrastructure.fonts import _font_cache, font_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_selection_is_shared_by_download_and_offline_apply(tmp_path: Path) -> None:
    initial = AptCommands(tmp_path / "APT cache with spaces", ("foot",))
    frozen = initial.freeze(["foot=1.2", "dependency=2.3", str(tmp_path / "happ.deb")])
    online, offline = frozen.download(), frozen.apply()
    assert initial.selection == ("foot",)
    assert online[online.index("install") + 1 :] == offline[offline.index("install") + 1 :]
    assert offline[:4] == ["/usr/bin/unshare", "--net", "--", "/usr/bin/apt-get"]
    assert "--download-only" in online and "--no-download" in offline
    for command in (frozen.simulate(), online, offline):
        assert "--no-remove" in command and "--no-install-recommends" in command
        assert "Acquire::AllowInsecureRepositories=false" in command
        assert "APT::Get::AllowUnauthenticated=false" in command
    assert not any("127.0.0.1" in argument for argument in offline)
    assert "Acquire::http::Proxy=DIRECT" in offline
    assert "Acquire::https::Proxy=DIRECT" in offline
    online.clear()
    assert frozen.download() and frozen.selection == (
        "foot=1.2",
        "dependency=2.3",
        str(tmp_path / "happ.deb"),
    )
    assert not list(tmp_path.iterdir())


def test_sources_use_one_official_origin_and_explicit_keyring(tmp_path: Path) -> None:
    keyring = tmp_path / "keyring with spaces.pgp"
    sources = AptSources("trixie", "trixie-backports", keyring)
    managed = sources.managed_backports().decode()
    staged = sources.staging()
    assert "Suites: trixie-backports\n" in managed
    assert "Suites: trixie trixie-updates trixie-backports\n" in staged
    assert "Suites: trixie-security\n" in staged
    assert "URIs: https://deb.debian.org/debian\n" in managed
    assert "URIs: https://deb.debian.org/debian\n" in staged
    assert "URIs: https://security.debian.org/debian-security\n" in staged
    assert managed.count(f"Signed-By: {keyring}") == 1
    assert staged.count(f"Signed-By: {keyring}") == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "metadata",
    [
        "Package: happ\nVersion: 4.5.4-333\nArchitecture: arm64\n",
        "Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\nPackage: happ\n",
        "Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\nUnexpected: field\n",
        "Package: happ\nVersion: other\nArchitecture: amd64\n",
    ],
)
def test_pinned_happ_identity_rejects_changed_or_duplicate_metadata(metadata: str) -> None:
    pin = HappPin(
        digest="1" * 64, version="4.5.4-333", architecture="amd64", url="https://github.com/example"
    )
    pin.validate_metadata("Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\n")
    assert pin.cache_name() == "happ_4.5.4-333_amd64.deb"
    with pytest.raises(ValueError, match="metadata"):
        pin.validate_metadata(metadata)


def test_font_cache_names_follow_changed_manifest_pins(tmp_path: Path) -> None:
    root = tmp_path / "source"
    (root / "manifests").mkdir(parents=True)
    (root / "assets/hyprland").mkdir(parents=True)
    (root / "manifests/platforms.toml").write_bytes(
        (ROOT / "manifests/platforms.toml").read_bytes()
    )
    font = (ROOT / "manifests/fonts.toml").read_text()
    current = font_manifest(ROOT).jetbrains_mono_nerd_font
    version, digest = "3.5.6", "1" * 64
    font = font.replace(current.version, version).replace(current.sha256, digest)
    (root / "manifests/fonts.toml").write_text(font)
    policy = font_manifest(root).jetbrains_mono_nerd_font
    name = policy.cache_name
    assert name == f"JetBrainsMono-{version}-{digest}.zip"
    archive, receipt, _ = _font_cache(policy)
    assert archive.name == name
    assert receipt.name == name + ".json"
