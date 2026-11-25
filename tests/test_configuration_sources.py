"""Exercise settings precedence and trust boundaries in the two supported runtimes."""

import os
import subprocess
import sys
import tomllib

import pytest
import tomlkit
from pydantic import ValidationError

from setup_system.configuration import ApplicationOptions, Settings
from setup_system.configuration import sources as settings_sources
from setup_system.layout import resource_root


def test_stage_precedence_and_partial_tables(tmp_path, monkeypatch):
    store = Settings(tmp_path, tmp_path / "config")
    store.directory.mkdir(parents=True, mode=0o700)
    path = store.directory / "workspace.toml"
    path.write_text('apps=["codex"]\n[theme]\nrounding=4\n')
    path.chmod(0o600)
    monkeypatch.setenv("SETUP_SYSTEM_LANGUAGES", '["override"]')
    loaded = store.load("workspace", profile="example", confirmed={"theme": {"effects": True}})
    assert loaded.options.apps == ("codex",)
    assert loaded.options.languages == ("python",)
    assert loaded.options.theme.rounding == 4 and loaded.options.theme.effects
    assert loaded.options.theme.opacity == store.load("workspace").options.theme.opacity
    assert not store.load("workspace").options.languages
    assert not store.load("base").options.hyprland
    confirmed = store.load("workspace", profile="example", confirmed={"apps": []})
    assert not confirmed.options.apps
    assert path.read_text() == 'apps=["codex"]\n[theme]\nrounding=4\n'


@pytest.mark.parametrize("key", ["_env_file", "_secrets_dir", "_cli_parse_args", "root", "target"])
def test_confirmed_source_cannot_change_loader_or_execution_policy(tmp_path, key):
    store = Settings(tmp_path, tmp_path / "config")
    with pytest.raises(ValidationError) as error:
        store.load("base", confirmed={key: "private input value"})
    assert "private input value" not in str(error.value)
    assert not store.directory.exists()


def test_confirmed_value_cannot_hide_invalid_saved_preferences(tmp_path):
    store = Settings(tmp_path, tmp_path / "config")
    store.directory.mkdir(parents=True, mode=0o700)
    path = store.directory / "base.toml"
    path.write_text("hyprland = 1")
    path.chmod(0o600)
    with pytest.raises(ValidationError):
        store.load("base", confirmed={"hyprland": True})
    assert path.read_text() == "hyprland = 1"


def test_only_packaged_profile_and_fixed_section_names_are_read(tmp_path):
    store = Settings(tmp_path, tmp_path / "config")
    with pytest.raises(ValueError, match="profile"):
        store.load("base", profile="../private")
    with pytest.raises(ValueError, match="section"):
        store.load("../private")
    assert not store.directory.exists()


def test_only_named_numeric_application_environment_overrides_packaged_toml(tmp_path, monkeypatch):
    packaged = tomllib.loads((resource_root() / "config/application.toml").read_text())
    monkeypatch.chdir(tmp_path)
    (tmp_path / "application.toml").write_text("untrusted = 1")
    (tmp_path / ".env").write_text("SETUP_SYSTEM_SUGGESTION_RESULTS=99")
    (tmp_path / "pyproject.toml").write_text("[tool.pydantic-settings]\nsuggestion_results=99")
    for field in ApplicationOptions.model_fields:
        monkeypatch.delenv(f"SETUP_SYSTEM_{field.upper()}", raising=False)
    monkeypatch.setenv("setup_system_suggestion_results", "98")
    monkeypatch.setenv("SETUP_SYSTEM_TARGET", "admin-apply")
    assert Settings.application().model_dump() == packaged
    monkeypatch.setenv("SETUP_SYSTEM_SUGGESTION_RESULTS", "9")
    monkeypatch.setenv("SETUP_SYSTEM_IDLE_TIMEOUT_SECONDS", "3.5")
    assert Settings.application().model_dump() == packaged | {
        "suggestion_results": 9,
        "idle_timeout_seconds": 3.5,
    }


@pytest.mark.parametrize("raw", ["0", "513", "true", "NaN", "Infinity", "quoted secret"])
def test_invalid_application_environment_rejected_without_echoing_input(monkeypatch, raw):
    monkeypatch.setenv("SETUP_SYSTEM_SUGGESTION_RESULTS", raw)
    with pytest.raises(ValueError) as raised:
        Settings.application()
    assert "quoted secret" not in str(raised.value)


def test_packaged_toml_requires_declared_types_and_safe_location(tmp_path, monkeypatch):
    package = tmp_path / "package"
    config = package / "config"
    config.mkdir(parents=True)
    source = resource_root() / "config/application.toml"
    path = config / "application.toml"
    monkeypatch.setattr(settings_sources, "resource_root", lambda: package)
    for field in ApplicationOptions.model_fields:
        monkeypatch.delenv(f"SETUP_SYSTEM_{field.upper()}", raising=False)
    path.write_text(source.read_text() + '\nunknown = "ignore-me"\n')
    with pytest.raises(ValidationError):
        Settings.application()
    invalid = tomlkit.parse(source.read_text())
    invalid["suggestion_results"] = True
    path.write_text(tomlkit.dumps(invalid))
    with pytest.raises(ValidationError):
        Settings.application()
    # An override must not hide a missing field in the shipped package.
    del invalid["suggestion_results"]
    path.write_text(tomlkit.dumps(invalid))
    monkeypatch.setenv("SETUP_SYSTEM_SUGGESTION_RESULTS", "4")
    with pytest.raises(ValidationError):
        Settings.application()
    path.unlink()
    path.symlink_to(source)
    with pytest.raises(ValueError, match="unsafe"):
        Settings.application()


def test_import_does_not_load_configuration_or_discover_the_host(tmp_path):
    code = """
import sys

def monitor(event, args):
    if event == 'open' and isinstance(args[0], str) and (
        args[0].endswith(('.toml', '.env')) or args[0] in ('/etc/passwd', '/etc/os-release')
    ):
        raise AssertionError(args[0])
    if event in ('socket.connect', 'subprocess.Popen'):
        raise AssertionError(event)

sys.addaudithook(monitor)
from setup_system.configuration import Settings
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=os.environ | {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
