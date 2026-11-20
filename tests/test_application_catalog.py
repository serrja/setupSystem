"""Validate release data and exercise a real local Debian archive without installing it."""

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from setup_system.domain.catalog import components, selected_components
from setup_system.domain.settings import UserSettings
from setup_system.downloads import DownloadError, DownloadFailure, DownloadResult
from setup_system.infrastructure import catalog_deb
from setup_system.infrastructure.app_configs import codex_settings


def test_every_component_round_trips_through_selection() -> None:
    for entry in components():
        key = {"app": "apps", "language": "language_profiles", "optional": "optional_apps"}[
            entry.kind
        ]
        settings = UserSettings.model_validate({key: [entry.id]})
        assert selected_components(settings) == (entry,)
    with pytest.raises(ValueError, match="catalog"):
        UserSettings.model_validate({"apps": ["arbitrary-executor"]})


def test_codex_preserves_account_choices_and_permission_profiles() -> None:
    existing = b'# owner choice\nmodel="my-model"\nmodel_reasoning_effort="medium"\n'
    existing += b'default_permissions="private"\n'
    result = codex_settings(existing)
    parsed = tomllib.loads(result.decode())
    assert b"# owner choice" in result
    assert parsed["model_reasoning_effort"] == "medium" and parsed["model"] == "my-model"
    assert parsed["default_permissions"] == "private" and "sandbox_mode" not in parsed
    assert tomllib.loads(codex_settings(b"").decode()) == {
        "sandbox_mode": "workspace-write",
        "approval_policy": "on-request",
    }


def test_release_policy_pin_matches_repository_instructions() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = tomllib.loads((root / "manifests/projects.toml").read_text())
    baseline = next(
        item["policy"] for item in catalog["project"] if item["id"] == "serrja/baseRules"
    )
    assert f"/blob/{baseline['commit']}/{baseline['document']}" in (root / "AGENTS.md").read_text()


def test_catalog_archive_verification_retry_and_readback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    package = tmp_path / "pkg/DEBIAN"
    package.mkdir(parents=True)
    package.chmod(0o755)
    (package / "control").write_text(
        "Package: chatgpt\nVersion: 1.0\nArchitecture: amd64\n"
        "Maintainer: Fixture <test@example.invalid>\nDescription: synthetic data\n"
    )
    archive = tmp_path / "test.deb"
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(package.parent), str(archive)], check=True
    )
    data = archive.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest = manifests / "applications.toml"
    manifest.write_text(
        '[chatgpt]\nhandler="catalog_deb"\n[chatgpt.artifacts.amd64]\nversion="1.0"\n'
        f'url="https://persistent.oaistatic.com/pinned.deb"\nsha256="{sha}"\n'
    )
    records = tmp_path / "attempts"
    monkeypatch.setattr(catalog_deb, "ATTEMPTS", records)

    # System state is redirected to the private fixture. No host paths are opened.
    def record(path, content, mode=0o600):
        assert path.parent == records
        records.mkdir(exist_ok=True)
        path.write_bytes(content)

    monkeypatch.setattr(catalog_deb, "_write_system", record)

    def fail(*args):
        raise DownloadError(DownloadFailure.HTTP, host="persistent.oaistatic.com", status=403)

    monkeypatch.setattr(catalog_deb, "copy_https", fail)
    (tmp_path / "first").mkdir()
    with pytest.raises(DownloadError):
        catalog_deb.stage_deb(tmp_path, "chatgpt", "amd64", tmp_path / "first")
    row = json.loads((records / "chatgpt.json").read_bytes())
    assert row["time"] and "403" in row["result"]

    def acquire(url, target, policy):
        target.write(data)
        return DownloadResult(len(data), sha)

    monkeypatch.setattr(catalog_deb, "copy_https", acquire)
    stage = tmp_path / "retry"
    stage.mkdir()
    assert catalog_deb.stage_deb(tmp_path, "chatgpt", "amd64", stage).read_bytes() == data
    catalog_deb.record_reported_attempts(capsys.readouterr().out)
    assert "verified" in catalog_deb.last_attempt("chatgpt")
    with pytest.raises(ValueError, match="No verified"):
        catalog_deb.stage_deb(tmp_path, "chatgpt", "arm64", stage)
    manifest.write_text(manifest.read_text().replace(sha, "0" * 64))
    (tmp_path / "changed").mkdir()
    with pytest.raises(ValueError, match="checksum"):
        catalog_deb.stage_deb(tmp_path, "chatgpt", "amd64", tmp_path / "changed")
    catalog_deb.record_reported_attempts(capsys.readouterr().out)
    assert "checksum" in catalog_deb.last_attempt("chatgpt")


@pytest.mark.parametrize(
    ("current", "active", "accepted"),
    [
        ("1.0", 3, True),
        ("1.0", 0, False),
        ("2.0", 3, False),
        (None, 3, False),
    ],
)
def test_happ_upgrade_requires_confirmed_version_and_disconnected_service(
    current, active, accepted
):
    from setup_system.installation import _check_existing_happ

    def command(argv, **kwargs):
        if argv[0] == "/usr/bin/dpkg-query":
            state = f"install ok installed\n{current}\n" if current else ""
            return subprocess.CompletedProcess(argv, 0 if current else 1, state)
        assert argv == ["/usr/bin/systemctl", "--quiet", "is-active", "happd.service"]
        return subprocess.CompletedProcess(argv, active)

    if accepted:
        _check_existing_happ("3.0", expected_happ_version="1.0", run=command)
    else:
        with pytest.raises(ValueError):
            _check_existing_happ("3.0", expected_happ_version="1.0", run=command)


def test_codex_delivers_verified_policy_in_effective_override(tmp_path, monkeypatch):
    import io
    import tarfile

    from setup_system.infrastructure import app_configs

    home = tmp_path / "home"
    config = home / ".codex"
    config.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(config))
    for kind in ("CONFIG", "STATE", "CACHE", "DATA"):
        monkeypatch.setenv("XDG_" + kind + "_HOME", str(home / kind.lower()))
    preferences = 'model = "owner-choice"\nsandbox_mode = "read-only"\napproval_policy = "never"\n'
    (config / "config.toml").write_text(preferences)
    (config / "AGENTS.md").write_text("Lower priority owner instructions.\n")
    active = config / "AGENTS.override.md"
    active.write_text(
        "Keep my heading.\n\n# >>> setupSystem >>>\nObsolete policy pointer.\n"
        "# <<< setupSystem <<<\n\nKeep my final paragraph.\n"
    )
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "source"
    manifests = project / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "platforms.toml").write_bytes((root / "manifests/platforms.toml").read_bytes())
    metadata = (root / "manifests/projects.toml").read_text()
    rules = next(
        item["policy"]
        for item in tomllib.loads(metadata)["project"]
        if item["id"] == "serrja/baseRules"
    )
    contract = b"# Fixture contract\n\nUse the audited project root.\n"
    notice = b"Synthetic license notice.\n"
    (manifests / "projects.toml").write_text(
        metadata.replace(rules["sha256"], hashlib.sha256(contract).hexdigest())
    )
    resources = tmp_path / "resources"
    (resources / "catalogs").mkdir(parents=True)
    monkeypatch.setattr(app_configs, "RESOURCE_ROOT", resources)
    payload = b"#!/bin/sh\necho 'codex-cli 0.0.1'\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("codex")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    archive_data = buffer.getvalue()
    archive_url = "https://github.com/openai/codex/releases/download/fixture/codex.tar.gz"
    release = 'version = "0.0.1"\n'
    for arch in ("amd64", "arm64"):
        release += (
            f'[{arch}]\nurl = "{archive_url}"\nmember = "codex"\n'
            f'sha256 = "{hashlib.sha256(archive_data).hexdigest()}"\n'
        )
    (resources / "catalogs/codex.toml").write_text(release)
    upstream = "https://raw.githubusercontent.com/serrja/baseRules/" + rules["commit"]
    assets = {
        upstream + "/" + rules["document"]: contract,
        upstream + "/LICENSE": notice,
        archive_url: archive_data,
    }
    monkeypatch.setattr(app_configs, "read_https", lambda url, _: assets[url])
    app_configs.configure_codex(project)
    result = active.read_text()
    assert result.count(contract.decode().strip()) == 1
    assert rules["commit"] in result
    assert "Obsolete policy pointer." not in result
    assert "Keep my heading." in result and "Keep my final paragraph." in result
    assert (config / "AGENTS.md").read_text() == "Lower priority owner instructions.\n"
    assert (config / "config.toml").read_text() == preferences
    policy = home / "config/setup-system/policy"
    assert (policy / "engineering.md").read_bytes() == contract
    assert (policy / "LICENSE").read_bytes() == notice
    app_configs.configure_codex(project)
    assert active.read_text() == result
