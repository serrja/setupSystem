"""Decode previous known intentions with no changes to the original files."""

import pytest
import tomlkit
from pydantic import ValidationError

from setup_system.configuration.legacy import decode, translate
from setup_system.configuration.legacy_models import Theme, WorkspaceOptions


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


def test_palette_and_effect_ranges_are_validated() -> None:
    for values in ({"rounding": -1}, {"opacity": 0.1}, {"name": "unknown"}):
        with pytest.raises(ValidationError):
            Theme.model_validate(values)


@pytest.mark.parametrize(
    "data",
    [
        "schema_version=55",
        "schema_version=true",
        "firefox=1",
        "apps=[]",
        "stealth=true",
        "schema_version=2\napps='codex'",
        "[theme]\nname='unknown'",
    ],
)
def test_unknown_legacy_schema_preserves_source(tmp_path, data, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "settings.toml"
    source.write_text(data)
    with pytest.raises(ValueError):
        decode(source)
    assert source.read_text() == data and list(tmp_path.iterdir()) == [source]


def test_migration_maps_actions_and_preserves_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "settings.toml"
    raw = "# keep original\n" + tomlkit.dumps(
        {
            "firefox": True,
            "developer_tools": True,
            "language_profiles": ["python"],
            "theme": {"rounding": 3},
        }
    )
    source.write_text(raw)
    converted = translate(source)
    assert [(r.plugin, r.action) for r in converted.actions] == [
        ("git", "install"),
        ("zsh", "install"),
        ("firefox", "install"),
        ("python", "install"),
    ]
    assert converted.settings["hyprland"]["rounding"] == 3 and converted.settings["neovim"] == {
        "languages": ["python"]
    }
    assert source.read_text() == raw and list(tmp_path.iterdir()) == [source]
