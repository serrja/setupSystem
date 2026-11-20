"""Pure package/session policy stays typed without executing system commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from setup_system import installation
from setup_system.adapters.debian.package_session import (
    AptCommands,
    AptSources,
    HappPin,
    InstallJournal,
    InstallStage,
    InstallStatus,
)
from setup_system.font_policy import font_cache_name, load_font_archive
from setup_system.installation_state import InstallJournal as DomainJournal
from setup_system.installation_state import InstallStage as DomainStage
from setup_system.installation_state import InstallStatus as DomainStatus

ROOT = Path(__file__).resolve().parents[1]


def test_package_adapter_and_domain_share_one_lifecycle_vocabulary() -> None:
    assert InstallJournal is DomainJournal
    assert InstallStage is DomainStage
    assert InstallStatus is DomainStatus


def test_frozen_selection_is_shared_by_download_and_offline_apply(tmp_path: Path) -> None:
    initial = AptCommands(tmp_path / "APT cache with spaces", "http://127.0.0.1:18080", ("foot",))
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
    pin = HappPin("1" * 64, "4.5.4-333", "amd64")
    pin.validate_metadata("Package: happ\nVersion: 4.5.4-333\nArchitecture: amd64\n")
    assert pin.cache_name() == "happ_4.5.4-333_amd64.deb"
    with pytest.raises(ValueError, match="metadata"):
        pin.validate_metadata(metadata)


def test_typed_journal_tracks_failure_without_losing_completed_stages() -> None:
    journal = InstallJournal(observations={"observation": "preserved receipt"})
    journal.checkpoint(InstallStage.RESOLUTION, "metadata")
    journal.checkpoint(InstallStage.DOWNLOADS, "resolution")
    journal.failed(RuntimeError("Do not serialize exception content"))
    restored = InstallJournal.parse(journal.serialize().decode())
    assert restored.status == InstallStatus.FAILED
    assert restored.stage == InstallStage.DOWNLOADS
    assert restored.completed == ("metadata", "resolution")
    assert restored.error == "RuntimeError"
    assert restored.observations == journal.observations
    assert b"Do not serialize" not in journal.serialize()
    restored.merge_user("failed", None, ("user-downloads",))
    restored.merge_user("failed", None, ("user-downloads",))
    assert restored.completed == ("metadata", "resolution", "user-downloads")


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": True},
        {"schema": 2},
        {"stage": "unknown"},
        {"status": "unknown"},
        {"stage": None},
        {"status": None},
        {"completed": "packages"},
        {"completed": [1]},
        {"error": {"not": "a class name"}},
    ],
)
def test_malformed_typed_journal_is_rejected(changes: dict[str, object]) -> None:
    value = {"schema": 1, "stage": "metadata", "status": "running", "completed": [], **changes}
    with pytest.raises(ValueError, match="journal"):
        InstallJournal.parse(json.dumps(value))


def test_font_cache_and_root_preflight_follow_changed_manifest_pins(tmp_path: Path) -> None:
    root = tmp_path / "source"
    (root / "manifests").mkdir(parents=True)
    (root / "assets/hyprland").mkdir(parents=True)
    (root / "manifests/platforms.toml").write_bytes(
        (ROOT / "manifests/platforms.toml").read_bytes()
    )
    font = (ROOT / "manifests/fonts.toml").read_text()
    current = load_font_archive(ROOT)
    version, digest = "3.5.6", "1" * 64
    font = font.replace(current.version, version).replace(current.sha256, digest)
    (root / "manifests/fonts.toml").write_text(font)
    policy = load_font_archive(root)
    name = font_cache_name(version, digest)
    assert name == f"JetBrainsMono-{version}-{digest}.zip"
    assert f"cache:setup-system/downloads/{name}" in policy.preflight_paths()
    assert f"cache:setup-system/downloads/{name}.json" in policy.preflight_paths()
    home = tmp_path / "home"
    home.mkdir()
    target = installation.TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/bash")
    environment = installation.user_environment(target, {})
    archive = Path(environment["XDG_CACHE_HOME"]) / "setup-system/downloads" / name
    archive.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("Preserve owner file")
    archive.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        installation.validate_installation_paths(root, target, environment)
    assert outside.read_text() == "Preserve owner file"
