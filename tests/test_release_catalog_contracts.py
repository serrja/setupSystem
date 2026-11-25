"""Exercise native release resources, selection validation and settings source isolation."""

import pytest
from pydantic import ValidationError

from setup_system import layout
from setup_system.configuration import Settings
from setup_system_sdk import PluginSettings


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
        monkeypatch.setattr(layout, "__file__", str(module / "runner.py"))
        with pytest.raises(RuntimeError, match="missing"):
            layout.resource_root()
        (bundle / "Makefile").write_text("run:\n")
        assert layout.resource_root() == bundle


def test_locator_refuses_symlinked_data_and_release_ancestors(tmp_path, monkeypatch):
    bundle = tmp_path / "real"
    bundle.mkdir()
    (bundle / "Makefile").write_text("all:")
    source = tmp_path / "source"
    source.symlink_to(bundle)
    monkeypatch.setattr(layout, "__file__", str(source / "src/setup_system/runner.py"))
    with pytest.raises(ValueError, match="symlink"):
        layout.resource_root()


class FixtureSettings(PluginSettings):
    title: str = "default"


def test_declared_settings_sources_and_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TITLE", "private")
    (tmp_path / ".env").write_text("TITLE=private")
    store = Settings(tmp_path, tmp_path / "config")
    import asyncio

    draft = asyncio.run(store.read("example"))
    assert draft.values == {} and not store.directory.exists()
    saved = asyncio.run(store.publish("example", "default", {"title": "saved"}, 1, draft.revision))
    options = FixtureSettings.resolve(
        profile={"title": "profile"}, saved=saved.values, overrides={}
    )
    assert options.title == "saved"
    assert (
        FixtureSettings.resolve(profile={}, saved=saved.values, overrides={"title": "once"}).title
        == "once"
    )
    assert asyncio.run(store.read("example")).values == {"title": "saved"}


@pytest.mark.parametrize("key", ["_env_file", "_secrets_dir", "_env_prefix", "_cli_parse_args"])
def test_saved_fields_cannot_change_settings_reader_sources(key):
    with pytest.raises(ValidationError):
        FixtureSettings.resolve(profile={}, saved={key: "local"}, overrides={})
