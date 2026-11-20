"""Mock all privileged work and validate the root schema before any system command."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from setup_system.infrastructure import privileged_tasks as tasks
from setup_system.infrastructure.trusted_bootstrap import validate_settings


def test_root_source_import_is_stdlib_only() -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "import setup_system.infrastructure.privileged_tasks;"
            "assert 'pydantic' not in sys.modules;assert 'ansible_runner' not in sys.modules",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "settings", [{"command": "id"}, {"packages": "apt"}, {"packages": ["git"], "happ": "yes"}]
)
def test_untrusted_settings_rejected_before_source_or_package_side_effects(
    tmp_path: Path, settings: dict
) -> None:
    with pytest.raises(ValueError):
        validate_settings("workspace_packages", settings, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("path", ["/etc", "/root/.config", "../outside"])
def test_xdg_cannot_select_privileged_resources(tmp_path: Path, path: str) -> None:
    options = {
        "packages": ["reportbug"],
        "mode": "remove",
        "closure": ["reportbug"],
        "xdg": dict.fromkeys(("config", "data", "state", "cache"), path),
    }
    with pytest.raises(ValueError):
        validate_settings("cleanup", options, tmp_path)


def test_root_workspace_rejects_a_package_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tasks, "caller", lambda: SimpleNamespace(pw_name="fixture"))
    monkeypatch.setattr(tasks, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.system, "resolve_target", lambda _: object())
    monkeypatch.setattr(tasks.system, "user_environment", lambda *args: {})
    monkeypatch.setattr(
        tasks, "_packages", lambda *args, **kwargs: pytest.fail("Unexpected package applied")
    )
    with pytest.raises(ValueError, match="allowlisted"):
        tasks.execute(
            Path(__file__).resolve().parents[1],
            "fixture",
            "workspace_packages",
            {"packages": ["openssh-server-"]},
        )


def test_root_package_action_uses_only_selected_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(tasks, "caller", lambda: SimpleNamespace(pw_name="fixture"))
    monkeypatch.setattr(tasks, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.system, "resolve_target", lambda _: object())
    monkeypatch.setattr(tasks.system, "user_environment", lambda *args: {})
    monkeypatch.setattr(
        tasks, "_packages", lambda root, packages, **kwargs: calls.append((packages, kwargs))
    )
    assert (
        tasks.execute(
            Path(__file__).resolve().parents[1],
            "fixture",
            "workspace_packages",
            {"packages": ["git"], "happ": False},
        )
        == 0
    )
    assert calls == [(("git",), {"happ": False, "external": (), "expected_happ_version": None})]


def test_base_apply_revalidates_then_applies_finite_stages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from contextlib import contextmanager

    from setup_system.installation import TargetUser

    home = tmp_path / "home"
    home.mkdir()
    target = TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/sh")
    calls = []
    monkeypatch.setattr(tasks, "caller", lambda: SimpleNamespace(pw_name="fixture"))
    monkeypatch.setattr(tasks, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.system, "resolve_target", lambda _: target)
    real_path = Path
    monkeypatch.setattr(
        tasks,
        "Path",
        lambda value: (
            tmp_path / "system.toml"
            if value == "/etc/setup-system/system.toml"
            else real_path(value)
        ),
    )

    class Desktop:
        def __init__(self, *args, **kwargs):
            pass

        def plan(self, *args, **kwargs):
            calls.append("plan")
            return SimpleNamespace(extra_packages=())

        def revalidate(self, plan):
            calls.append("revalidate")
            return plan

        def apply(self, plan):
            calls.append("desktop")

    @contextmanager
    def packages(*args, **kwargs):
        yield SimpleNamespace(install=lambda: calls.append("packages"))

    monkeypatch.setattr(tasks, "DesktopSystem", Desktop)
    monkeypatch.setattr(tasks.system, "prepare_packages", packages)
    monkeypatch.setattr(tasks, "_save_system", lambda settings, **kwargs: calls.append("settings"))
    options = {
        "schema_version": 1,
        "hyprland": True,
        "graphical_login": True,
        "timezone": None,
        "keyboard": "us",
        "plymouth": False,
        "expected_system_sha256": None,
        "xdg": {name: str(home / name) for name in ("config", "data", "state", "cache")},
    }
    assert tasks.execute(tmp_path, "fixture", "base", options) == 0
    assert calls == ["packages"]
    assert tasks.execute(tmp_path, "fixture", "login", options) == 0
    assert tasks.execute(tmp_path, "fixture", "system_settings", options) == 0
    assert calls == ["packages", "plan", "revalidate", "desktop", "settings"]


def test_stale_system_revision_stops_before_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from setup_system.installation import TargetUser

    home = tmp_path / "home"
    home.mkdir()
    target = TargetUser("fixture", os.getuid(), os.getgid(), home, "/bin/sh")
    path = tmp_path / "system.toml"
    path.write_text("schema_version=1\n")
    monkeypatch.setattr(tasks, "caller", lambda: SimpleNamespace(pw_name="fixture"))
    monkeypatch.setattr(tasks, "protected", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.system, "resolve_target", lambda _: target)
    monkeypatch.setattr(tasks, "Path", lambda _: path)
    monkeypatch.setattr(
        tasks.system,
        "prepare_packages",
        lambda *args, **kwargs: pytest.fail("Stale settings reached APT"),
    )
    with pytest.raises(ValueError, match="changed after"):
        tasks.execute(tmp_path, "fixture", "base", {"expected_system_sha256": None})


def test_new_firefox_policy_is_readable_through_each_created_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "protected", lambda *args, **kwargs: None)
    path = tmp_path / "etc/firefox/policies/policies.json"
    tasks._firefox_policy(path)
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert directory.stat().st_mode & 0o005 == 0o005
    assert path.stat().st_mode & 0o004
    assert "downloads/latest/uBlock0@raymondhill.net/latest.xpi" in path.read_text()
    path.write_text('{"policies":{"DisableTelemetry":true}}')
    with pytest.raises(ValueError, match="manual review"):
        tasks._firefox_policy(path)


def test_known_private_firefox_policy_migrates_without_exposing_other_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "protected", lambda *args, **kwargs: None)
    path = tmp_path / "etc/firefox/policies/policies.json"
    backups = tmp_path / "backups"
    tasks._firefox_policy(path, backups=backups)
    legacy = path.read_bytes().replace(
        b"/uBlock0@raymondhill.net/latest.xpi", b"/ublock-origin/latest.xpi"
    )
    path.write_bytes(legacy)
    for directory in (path.parent.parent, path.parent):
        directory.chmod(0o700)
    other = path.parent / "admin.conf"
    other.touch()
    with pytest.raises(ValueError, match="unknown contents"):
        tasks._firefox_policy(path, backups=backups)
    assert not (path.parent.parent.stat().st_mode & 0o077)
    other.unlink()
    tasks._firefox_policy(path, backups=backups)
    assert all(directory.stat().st_mode & 0o001 for directory in (path.parent.parent, path.parent))
    assert next(backups.iterdir()).read_bytes() == legacy
