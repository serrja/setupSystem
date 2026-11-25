"""Desired-state validation and storage must not mix machine observations or secrets."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system.configuration import Settings
from setup_system.models import BaseOptions, Theme, WorkspaceOptions
from setup_system.suggestions import config_root


@pytest.fixture
def store(tmp_path):
    settings = Settings(tmp_path, tmp_path / "config")
    settings.directory.parent.mkdir(mode=0o700)
    settings.directory.mkdir(mode=0o700)
    return settings


def private_file(store, name, text):
    path = store.directory / name
    path.touch(mode=0o600)
    path.write_text(text)
    return path


def test_default_settings_select_no_optional_app_or_removal() -> None:
    desired = WorkspaceOptions()
    assert not desired.cleanup and not desired.languages
    assert not desired.apps
    assert not BaseOptions().hyprland
    import tomllib

    assert (
        tomllib.loads((config_root() / "shared/palette.toml").read_text())["name"]
        == "catppuccin_mocha_green"
    )


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
        WorkspaceOptions.model_validate(invalid)


def test_store_preserves_comments_backups_and_conflict_detection(store) -> None:
    path = private_file(store, "workspace.toml", "# Keep this comment\nschema_version=1\napps=[]\n")
    original = store.load("workspace")
    saved = store.save("workspace", WorkspaceOptions(apps=("firefox",)), original.revision)
    assert saved.revision != original.revision
    assert "# Keep this comment" in path.read_text()
    assert "firefox" in store.load("workspace").options.apps
    backups = list(store.directory.glob("workspace.toml.bak-*"))
    assert len(backups) == 1 and "apps=[]" in backups[0].read_text()
    with pytest.raises(ValueError, match="changed"):
        store.save("workspace", original.options, original.revision)


def test_schema_migration_is_read_only_and_future_versions_fail(store) -> None:
    path = private_file(store, "settings.toml", "firefox = true\n")
    assert store.load("workspace").options.apps == ("firefox",)
    assert not (store.directory / "workspace.toml").exists()
    assert path.read_text() == "firefox = true\n"
    path.write_text("schema_version = 3\n")
    with pytest.raises(ValueError, match="Unknown settings schema"):
        store.load("workspace")


def test_symlink_and_group_writable_settings_are_rejected(store, tmp_path: Path) -> None:
    path = store.directory / "settings.toml"
    target = tmp_path / "other.toml"
    target.write_text("schema_version=1\n")
    path.symlink_to(target)
    with pytest.raises(OSError):
        store.load("workspace")
    path.unlink()
    path.write_text("schema_version=1\n")
    path.chmod(0o666)
    with pytest.raises(ValueError, match="Unsafe"):
        store.load("workspace")


def test_palette_and_effect_ranges_are_validated() -> None:
    for values in ({"rounding": -1}, {"opacity": 0.1}, {"name": "unknown"}):
        with pytest.raises(ValidationError):
            Theme.model_validate(values)


def test_nested_theme_comments_and_cleared_optional_timezone_survive_save(store) -> None:
    path = private_file(
        store,
        "workspace.toml",
        "schema_version=1\n[theme]\n# keep nested comment\nrounding=12 # corner choice\n",
    )
    settings = store.load("workspace")
    store.save("workspace", WorkspaceOptions(theme=Theme(rounding=0)), settings.revision)
    assert "# keep nested comment" in path.read_text() and "# corner choice" in path.read_text()
    system = private_file(store, "base.toml", 'schema_version=1\ntimezone="Europe/Berlin"\n')
    store.save("base", BaseOptions(), store.load("base").revision)
    assert "timezone" not in system.read_text()


@pytest.mark.parametrize("future", [{"desktop_theme": True}, {"monitors": []}, {"apps": []}])
def test_v1_settings_reject_mixed_schemas(store, future):
    import tomlkit

    private_file(store, "settings.toml", tomlkit.dumps({"schema_version": 1, **future}))
    with pytest.raises(ValueError, match="version 1"):
        store.load("workspace")
