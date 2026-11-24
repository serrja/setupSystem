"""Exercise native release resources, selection validation and settings source isolation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system import runner
from setup_system.domain.applications import applications
from setup_system.domain.displays import MonitorLayout
from setup_system.models import BaseOptions, PlanSnapshot, WorkspaceOptions
from setup_system.settings import Settings
from setup_system.suggestions import Catalog


def test_explicit_source_and_wheel_roots_do_not_fall_back_to_installed_data(tmp_path, monkeypatch):
    for module, bundle in (
        (tmp_path / "source/src/setup_system", tmp_path / "source"),
        (
            tmp_path / "wheel/site-packages/setup_system",
            tmp_path / "wheel/site-packages/setup_system/bundle",
        ),
    ):
        module.mkdir(parents=True)
        bundle.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(runner, "__file__", str(module / "runner.py"))
        with pytest.raises(RuntimeError, match="missing"):
            runner.resource_root()
        (bundle / "Makefile").write_text("run:\n")
        assert runner.resource_root() == bundle


@pytest.mark.parametrize(
    "change",
    [
        {"handler": "shell"},
        {"kind": "script"},
        {"packages": ["--option"]},
        {"asset": {"other": "thing.deb"}},
        {"packages": []},
        {"github": "a/b", "asset": {"amd64": "{other}.deb"}, "packages": []},
        {"github": "a/b", "asset": {"amd64": "good.deb"}, "packages": []},
    ],
)
def test_explicit_catalogs_require_implemented_handlers(change):
    with pytest.raises(ValueError):
        applications(
            {"new_tool": {"title": "Example", "kind": "app", "packages": ["tree"], **change}}
        )


def test_settings_and_selection_do_not_read_files(monkeypatch):
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("Model read from disk"))
    settings = WorkspaceOptions.model_validate({"apps": ["firefox"]})
    assert settings.apps == ("firefox",)


@pytest.mark.parametrize(
    ("model", "change"),
    [
        (BaseOptions(), {"hyprland": 1}),
        (WorkspaceOptions(), {"unknown": True}),
        (WorkspaceOptions(), {"apps": ["firefox", "firefox"]}),
        (WorkspaceOptions(), {"theme": {"effects": "true"}}),
        (MonitorLayout(output="DP-1", mode="1024x768@60", scale=1, x=0, y=0), {"x": True}),
        (
            MonitorLayout(output="DP-1", mode="1024x768@60", scale=1, x=0, y=0),
            {"scale": float("inf")},
        ),
    ],
)
def test_validated_updates_reject_undeclared_or_lossy_values(model, change):
    with pytest.raises(ValidationError):
        type(model).model_validate({**model.model_dump(), **change})


def test_live_source_identity_is_detached_from_mutable_editor_metadata():
    source = {"editor": {"repo": "https://github.com/folke/lazy.nvim", "version": "a" * 40}}
    plan = PlanSnapshot.capture(
        "workspace",
        {"apps": ["neovim"]},
        {
            "status": "ready",
            "actions": ["install"],
            "observed": {"tools": source, "needs_system": False, "needs_user": True},
        },
        None,
    )
    source["editor"]["version"] = "b" * 40
    assert plan.payload()["expected"]["observed"]["tools"]["editor"]["version"] == "a" * 40


def test_locator_refuses_symlinked_data_and_release_ancestors(tmp_path, monkeypatch):
    bundle = tmp_path / "real"
    bundle.mkdir()
    (bundle / "Makefile").write_text("all:")
    source = tmp_path / "source"
    source.symlink_to(bundle)
    monkeypatch.setattr(runner, "__file__", str(source / "src/setup_system/runner.py"))
    with pytest.raises(ValueError, match="symlink"):
        runner.resource_root()
    with pytest.raises(ValueError, match="symlink"):
        Catalog(source / "config")


def test_declared_settings_sources_and_precedence(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.chdir(home)
    (home / ".env").write_text("SETUP_SYSTEM_HYPRLAND=true\n")
    monkeypatch.setenv("SETUP_SYSTEM_HYPRLAND", "true")
    store = Settings(home, home / "config")
    loaded = store.load("base")
    assert not loaded.options.hyprland and not store.directory.exists()
    store.save("base", BaseOptions(hyprland=True), loaded.revision)
    assert store.load("base").options.hyprland


@pytest.mark.parametrize("key", ["_env_file", "_secrets_dir", "_env_prefix", "_cli_parse_args"])
def test_toml_cannot_change_settings_reader_sources(tmp_path, key):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    store = Settings(home, home / "config")
    store.directory.mkdir(parents=True, mode=0o700)
    (store.directory / "workspace.toml").write_text(f'schema_version=1\n{key}="local"\n')
    with pytest.raises(ValueError):
        store.load("workspace")
