"""Release isolation, immutable selection snapshots and early stdlib delivery."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system.domain.displays import MonitorLayout
from setup_system.domain.editor_lock import EditorLock
from setup_system.domain.settings import SystemSettings, ThemeSettings, UserSettings
from setup_system.infrastructure.catalog_repository import load_components, load_editor
from setup_system.infrastructure.config_store import ConfigStore
from setup_system.infrastructure.theme import preview
from setup_system.resource_locator import ResourceLocator

ROOT = Path(__file__).resolve().parents[1]


def release(parent: Path, name: str, *, source: bool, color: str) -> Path:
    root = parent / name
    (root / "manifests").mkdir(parents=True)
    shutil.copyfile(ROOT / "manifests/platforms.toml", root / "manifests/platforms.toml")
    (root / "manifests/applications.toml").write_text(
        f'[{name}]\nkind = "app"\ntitle = "Reviewed application"\n'
        'handler = "apt"\npackages = ["tree"]\n'
    )
    resources = root / ("src/setup_system/resources" if source else "resources")
    (resources / "themes").mkdir(parents=True)
    palette = "themes/catppuccin_mocha_green.toml"
    original = ResourceLocator.for_root(ROOT).resource(palette)
    (resources / palette).write_text(original.read_text().replace("#a6e3a1", color))
    return root


def test_explicit_source_and_wheel_roots_do_not_fall_back_to_installed_data(tmp_path):
    source = release(tmp_path, "first_app", source=True, color="#123456")
    wheel = release(tmp_path, "next_app", source=False, color="#654321")
    first = load_components(source)
    second = load_components(wheel)
    settings = UserSettings(apps=("first_app",))
    assert first.selected(settings)[0].id == "first_app"
    assert "#123456" in preview(settings.theme, root=source)
    assert "#654321" in preview(settings.theme, root=wheel)
    with pytest.raises(ValueError, match="catalog selection"):
        second.validate_settings(settings)
    (source / "manifests/applications.toml").unlink()
    with pytest.raises(FileNotFoundError):
        load_components(source)
    assert first.selected(settings)[0].id == "first_app"
    store = ConfigStore(tmp_path / "intentions.toml", UserSettings, catalog=first)
    assert b"first_app" in store.prepare(settings, None)
    assert not store.path.exists()


@pytest.mark.parametrize(
    ("kind", "handler", "name"),
    [
        ("language", "language", "unknown_language"),
        ("app", "firefox", "another_browser"),
        ("optional", "firefox", "firefox"),
        ("optional", "catalog_deb", "third_party"),
        ("language", "apt", "typescript_vue"),
    ],
)
def test_explicit_catalogs_require_implemented_handlers(tmp_path, kind, handler, name):
    root = release(tmp_path, name, source=True, color="#123456")
    (root / "manifests/applications.toml").write_text(
        f'[{name}]\nkind="{kind}"\ntitle="Fixture"\nhandler="{handler}"\npackages=["tree"]\n'
    )
    with pytest.raises(ValueError):
        load_components(root)


def test_settings_and_selection_do_not_read_files(monkeypatch):
    catalog = load_components(ROOT)

    def forbid(*args, **kwargs):
        pytest.fail("A domain operation performed filesystem I/O")

    monkeypatch.setattr(Path, "open", forbid)
    settings = UserSettings().validated(apps=["firefox"])
    assert catalog.selected(settings)[0].id == "firefox"


@pytest.mark.parametrize(
    ("model", "change"),
    [
        (ThemeSettings(), {"rounding": True}),
        (ThemeSettings(), {"opacity": float("nan")}),
        (SystemSettings(), {"schema_version": True}),
        (SystemSettings(), {"hyprland": 1}),
        (UserSettings(), {"schema_version": 2.0}),
        (UserSettings(), {"apps": ["firefox", "firefox"]}),
        (UserSettings(), {"unknown_selection": True}),
        (UserSettings(), {"theme": {"effects": "true"}}),
        (
            MonitorLayout(output="DP-1", mode="1024x768@60", scale=1, x=0, y=0),
            {"scale": float("inf")},
        ),
        (MonitorLayout(output="DP-1", mode="1024x768@60", scale=1, x=0, y=0), {"x": True}),
    ],
)
def test_validated_updates_reject_undeclared_or_lossy_values(model, change):
    before = model.model_dump()
    with pytest.raises(ValidationError):
        model.validated(**change)
    assert model.model_dump() == before


@pytest.mark.parametrize("mutation", ["unknown", "bad_digest", "missing_pair", "mutable_version"])
def test_editor_lock_rejects_invalid_nested_release_inputs(mutation):
    document = tomllib.loads((ROOT / "manifests/editor.toml").read_text())
    match mutation:
        case "unknown":
            document["flutter"]["script"] = "/bin/sh"
        case "bad_digest":
            document["neovim"]["linux_x86_64_sha256"] = "invalid"
        case "missing_pair":
            del document["neovim"]["linux_arm64_sha256"]
        case "mutable_version":
            document["version"] = True
    with pytest.raises(ValidationError):
        EditorLock.model_validate(document)


def test_editor_lock_shares_no_mutable_nested_mappings():
    lock = load_editor(ROOT)
    with pytest.raises(TypeError):
        lock.plugins["lazy"] = lock.plugins["lazy"]
    copy = lock.model_dump(mode="json", by_alias=True)
    copy["plugins"].clear()
    assert "lazy" in lock.plugins


def test_locator_refuses_symlinked_data_and_release_ancestors(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "resources").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ResourceLocator.for_root(tmp_path).resource("themes/example.toml")
    source = tmp_path / "source"
    source.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        ResourceLocator.for_root(source / "subdirectory")


def test_delivery_import_with_system_stdlib_does_not_read_manifests():
    script = """
import pathlib
import sys
import tomllib
sys.path.insert(0, sys.argv[1])
def unexpected_read(*args, **kwargs):
    raise AssertionError("Import read release data")
pathlib.Path.open = unexpected_read
tomllib.load = tomllib.loads = unexpected_read
import setup_system._delivery_entry
import setup_system.infrastructure.user_storage
assert "pydantic" not in sys.modules
"""
    subprocess.run([sys.executable, "-IBS", "-c", script, str(ROOT / "src")], check=True)


def test_declared_settings_sources_and_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYPRLAND", "true")
    (tmp_path / ".env").write_text("SETUP_SYSTEM_HYPRLAND=true\n")
    store = ConfigStore(tmp_path / "system.toml", SystemSettings)
    assert not store.read()[0].hyprland
    monkeypatch.setenv("SETUP_SYSTEM_HYPRLAND", "true")
    assert store.read()[0].hyprland
    store.path.write_text("schema_version=1\nhyprland=false\n")
    monkeypatch.setenv("SETUP_SYSTEM_HYPRLAND", "invalid lower-priority value")
    assert not store.read()[0].hyprland
    store.path.write_text('schema_version=1\nhyprland="true"\n')
    with pytest.raises(ValidationError):
        store.read()


@pytest.mark.parametrize("key", ["_env_file", "_secrets_dir", "_env_prefix", "_cli_parse_args"])
def test_toml_cannot_change_settings_reader_sources(tmp_path, key):
    config = tmp_path / "user.toml"
    config.write_text(f'schema_version=2\n{key}="local"\n')
    with pytest.raises(ValueError, match="configuration sources"):
        ConfigStore(config, UserSettings).read()
