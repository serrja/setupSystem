"""Exercise native catalog resolution, Debian archive identity and safe Codex merges."""

import hashlib
import runpy
import subprocess
import tomllib
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

from setup_system.file_tasks import restore
from setup_system.infrastructure import codex_profile, happ_facts
from setup_system.infrastructure.managed_files import apply_files
from setup_system.suggestions import Catalog

ROOT = Path(__file__).resolve().parents[1]


def test_every_component_round_trips_through_selection() -> None:
    config = ROOT / "config"
    picker = Catalog(config)
    entries = tomllib.loads((config / "workspace/applications.toml").read_text())
    validate = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))["validate_request"]
    for name, entry in entries.items():
        field = "languages" if entry["kind"] == "language" else "apps"
        assert getattr(picker, field)[name] == entry["title"]
        request = {"schema": 1, "settings": {field: [name]}, "expected": None}
        assert validate(request, "workspace", entries, picker.cleanup)[field] == [name]
    with pytest.raises(AnsibleFilterError, match="catalog"):
        validate(
            {"schema": 1, "settings": {"apps": ["unknown"]}, "expected": None},
            "workspace",
            entries,
            picker.cleanup,
        )


def test_codex_preserves_account_choices_and_permission_profiles() -> None:
    profile = b'# owner choice\nmodel="my-model"\nmodel_reasoning_effort="medium"\n'
    result = codex_profile.codex_settings(profile + b'default_permissions="private"\n')
    parsed = tomllib.loads(result.decode())
    assert b"# owner choice" in result
    assert parsed["model_reasoning_effort"] == "medium" and parsed["model"] == "my-model"
    assert parsed["default_permissions"] == "private" and "sandbox_mode" not in parsed
    assert tomllib.loads(codex_profile.codex_settings(b"").decode()) == {
        "sandbox_mode": "workspace-write",
        "approval_policy": "on-request",
    }


def test_release_policy_pin_matches_repository_instructions() -> None:
    baseline = tomllib.loads((ROOT / "config/workspace/rules.toml").read_text())
    assert f"/blob/{baseline['commit']}/{baseline['document']}" in (ROOT / "AGENTS.md").read_text()
    raw = (ROOT / "config/workspace/codex/engineering.md").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == baseline["sha256"]


def test_catalog_archive_verification_retry_and_readback(tmp_path):
    validate = runpy.run_path(str(ROOT / "ansible/files/apt_apply.py"))["validate_deb"]
    directory = tmp_path / "pkg/DEBIAN"
    directory.mkdir(parents=True, mode=0o755)
    directory.chmod(0o755)
    (directory / "control").write_text(
        "Package: happ\nVersion: 1.0-1\nArchitecture: amd64\n"
        "Maintainer: Fixture <test@example.invalid>\nDescription: Synthetic fixture\n"
    )
    archive = tmp_path / "test.deb"
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(directory.parent), str(archive)],
        check=True,
    )
    record = {
        "checksum": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
        "version": "1.0",
    }
    with pytest.raises(ValueError, match="changed"):
        validate(archive, "happ", {**record, "checksum": "sha256:" + "0" * 64}, "amd64")
    assert validate(archive, "happ", record, "amd64") == "1.0-1"
    with pytest.raises(ValueError, match="identity"):
        validate(archive, "happ", record, "arm64")


@pytest.mark.parametrize(
    ("current", "active", "accept"),
    [("1.0", False, True), ("1.0", True, False), ("2.0", False, False), (None, False, False)],
)
def test_happ_upgrade_requires_confirmed_version_and_disconnected_service(
    monkeypatch, current, active, accept
):
    inactive = {
        "ActiveState": "inactive",
        "LoadState": "loaded",
        "FragmentPath": "/usr/lib/systemd/system/happd.service",
    }
    monkeypatch.setattr(happ_facts, "installed_version", lambda: current)
    monkeypatch.setattr(
        happ_facts,
        "unit_info",
        lambda: dict(inactive, ActiveState="active") if active else inactive,
    )
    monkeypatch.setattr(
        happ_facts.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1)
    )
    expected = {"installed": "1.0", "unit": inactive}
    if accept:
        happ_facts.verify_before(expected, "3.0")
    else:
        with pytest.raises(ValueError):
            happ_facts.verify_before(expected, "3.0")


def test_codex_delivers_verified_policy_in_effective_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    directory = home / ".codex"
    directory.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(directory))
    for kind in ("CONFIG", "STATE", "CACHE", "DATA"):
        monkeypatch.setenv("XDG_" + kind + "_HOME", str(home / kind.lower()))
    preferences = 'model = "owner-choice"\nsandbox_mode = "read-only"\napproval_policy = "never"\n'
    (directory / "config.toml").write_text(preferences)
    notes = directory / "AGENTS.md"
    notes.write_text("Lower priority owner instructions.\n")
    active = directory / "AGENTS.override.md"
    text = "My heading.\n\n# >>> setupSystem >>>\nOld rules.\n# <<< setupSystem <<<\nMy ending.\n"
    active.write_text(text)
    (directory / "auth.json").symlink_to(tmp_path / "never-open")
    desired, before = codex_profile.files(ROOT / "config")
    receipt = apply_files(desired, expected=before)
    merged = active.read_text()
    assert merged.count((ROOT / "config/workspace/codex/engineering.md").read_text().strip()) == 1
    assert "My heading." in merged and "My ending." in merged and "Old rules." not in merged
    assert notes.read_text() == "Lower priority owner instructions.\n"
    assert (directory / "config.toml").read_text() == preferences
    assert codex_profile.files(ROOT / "config")[0] == desired
    assert (home / "config/setup-system/policy/LICENSE").read_bytes() == (
        ROOT / "config/workspace/codex/LICENSE"
    ).read_bytes()
    restore(receipt.name, apply=True)
    assert active.read_text() == text and not (home / "config/setup-system/policy/LICENSE").exists()


def test_failed_signed_source_is_separate_from_catalog_and_a_later_retry(tmp_path, monkeypatch):
    from setup_system.infrastructure import source_attempt

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert "No recorded" in source_attempt.previous()
    failed = source_attempt.observe(False, "https://example.invalid/?token=private 403 Forbidden")
    assert failed.checked.tzinfo and "HTTP 403" in failed.reason
    assert not (tmp_path / "state").exists()
    source_attempt.remember(failed)
    assert "HTTP 403" in source_attempt.previous()
    assert "private" not in source_attempt.previous()
    assert "chatgpt" in Catalog(ROOT / "config").apps
    source_attempt.remember(source_attempt.observe(True, ""))
    assert "verified" in source_attempt.previous()
