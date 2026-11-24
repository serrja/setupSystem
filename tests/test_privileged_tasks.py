"""Validate the native system boundary and migrate existing Firefox policies."""

import io
import json
import runpy
import sys
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError

from setup_system.infrastructure import firefox_policy
from setup_system.infrastructure.tool_paths import personal_path
from setup_system.runner import MakeRunner, Target

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "settings", [{"command": "id"}, {"packages": "apt"}, {"apps": ["arbitrary"]}]
)
def test_untrusted_settings_rejected_before_source_or_package_side_effects(tmp_path, settings):
    validate = runpy.run_path(str(ROOT / "ansible/filter_plugins/native.py"))["validate_request"]
    with pytest.raises((ValueError, AnsibleFilterError)):
        validate({"schema": 1, "settings": settings, "expected": None}, "workspace", {}, {})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("path", ["/etc", "/root/.config", "../outside"])
def test_xdg_cannot_select_privileged_resources(tmp_path, path):
    with pytest.raises(ValueError):
        personal_path(tmp_path, Path(path))
    runner = MakeRunner(tmp_path)
    with pytest.raises(ValueError, match="installed Debian"):
        runner.run(Target.BASE_APPLY, {})
    assert not list(tmp_path.iterdir())


def test_base_apply_requires_the_installed_program_before_any_system_write(tmp_path, monkeypatch):
    operation = runpy.run_path(str(ROOT / "ansible/files/system_contract.py"))["main"]
    monkeypatch.setattr(sys, "argv", ["system_contract.py", "login"])
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"settings": {"graphical_login": True}}))
    )
    with pytest.raises(ValueError, match="authenticated Debian"):
        operation()
    assert not list(tmp_path.iterdir())


def test_new_firefox_policy_is_readable_through_each_created_directory(tmp_path, monkeypatch):
    path = tmp_path / "etc/firefox/policies/policies.json"
    firefox_policy.apply(firefox_policy.inspect(path))
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert directory.stat().st_mode & 0o005 == 0o005
    assert path.stat().st_mode & 0o004
    assert "downloads/latest/uBlock0@raymondhill.net/latest.xpi" in path.read_text()
    path.write_text('{"policies":{"DisableTelemetry":true}}')
    with pytest.raises(ValueError, match="manual review"):
        firefox_policy.apply(firefox_policy.inspect(path))


def test_known_private_firefox_policy_migrates_without_exposing_other_files(tmp_path, monkeypatch):
    path = tmp_path / "etc/firefox/policies/policies.json"
    backups = tmp_path / "backups"
    firefox_policy.apply(firefox_policy.inspect(path), backups=backups)
    legacy = path.read_bytes().replace(
        b"/uBlock0@raymondhill.net/latest.xpi", b"/ublock-origin/latest.xpi"
    )
    path.write_bytes(legacy)
    for directory in (path.parent.parent, path.parent):
        directory.chmod(0o700)
    other = path.parent / "admin.conf"
    other.touch()
    with pytest.raises(ValueError, match="unknown contents"):
        firefox_policy.apply(firefox_policy.inspect(path), backups=backups)
    assert not (path.parent.parent.stat().st_mode & 0o077)
    other.unlink()
    firefox_policy.apply(firefox_policy.inspect(path), backups=backups)
    assert all(directory.stat().st_mode & 0o001 for directory in (path.parent.parent, path.parent))
    assert next(backups.iterdir()).read_bytes() == legacy
