"""Desired-state validation and storage must not mix machine observations or secrets."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system.domain.settings import SystemSettings, ThemeSettings, UserSettings
from setup_system.infrastructure.config_store import ConfigStore


def test_default_settings_select_no_optional_app_or_removal() -> None:
    desired = UserSettings()
    assert not desired.cleanup and not desired.optional_apps
    assert not any((desired.neovim, desired.happ, desired.codex, desired.firefox, desired.chatgpt))
    assert not SystemSettings().hyprland
    assert desired.theme.name == "catppuccin_mocha_green"


@pytest.mark.parametrize(
    "invalid",
    [
        {"token": "secret"},
        {"subscription": "https://private.invalid"},
        {"schema_version": 100},
        {"theme": {"opacity": float("nan")}},
    ],
)
def test_unknown_secret_and_unsupported_schema_fields_are_rejected(invalid: dict) -> None:
    with pytest.raises(ValidationError):
        UserSettings.model_validate(invalid)


def test_store_preserves_comments_backups_and_conflict_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = tmp_path / "settings.toml"
    path.write_text("# Keep this comment\nschema_version = 1\nfirefox = false\n")
    store = ConfigStore(path, UserSettings)
    original, revision = store.read()
    updated = original.model_copy(update={"firefox": True})
    new_revision = store.save(updated, revision)
    assert new_revision != revision
    assert "# Keep this comment" in path.read_text()
    assert store.read()[0].firefox
    assert list(tmp_path.glob("settings.toml.*.bak"))
    with pytest.raises(ValueError, match="changed"):
        store.save(original, revision)


def test_schema_migration_is_read_only_and_future_versions_fail(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("firefox = true\n")
    store = ConfigStore(path, UserSettings)
    assert store.read()[0].schema_version == 1
    assert path.read_text() == "firefox = true\n"
    path.write_text("schema_version = 2\n")
    with pytest.raises(ValidationError):
        store.read()


def test_symlink_and_group_writable_settings_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    target = tmp_path / "other.toml"
    target.write_text("schema_version=1\n")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        ConfigStore(path, UserSettings).read()
    path.unlink()
    path.write_text("schema_version=1\n")
    path.chmod(0o666)
    with pytest.raises(ValueError, match="writable"):
        ConfigStore(path, UserSettings).read()


def test_palette_and_effect_ranges_are_validated() -> None:
    for values in ({"rounding": -1}, {"opacity": 0.1}, {"name": "unknown"}):
        with pytest.raises(ValidationError):
            ThemeSettings.model_validate(values)


def test_nested_theme_comments_and_cleared_optional_timezone_survive_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = tmp_path / "settings.toml"
    path.write_text(
        "schema_version=1\n[theme]\n# keep nested comment\nrounding=12 # corner choice\n"
    )
    store = ConfigStore(path, UserSettings)
    settings, revision = store.read()
    store.save(settings.model_copy(update={"theme": ThemeSettings(rounding=0)}), revision)
    assert "# keep nested comment" in path.read_text() and "# corner choice" in path.read_text()
    system = tmp_path / "system.toml"
    system.write_text('schema_version=1\ntimezone="Europe/Berlin"\n')
    state = ConfigStore(system, SystemSettings)
    old, fingerprint = state.read()
    state.save(old.model_copy(update={"timezone": None}), fingerprint)
    assert "timezone" not in system.read_text()
