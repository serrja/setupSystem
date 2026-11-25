from pathlib import Path

import pytest
from pydantic import ValidationError

from setup_system.configuration import Settings
from setup_system.models import BaseOptions, PlanSnapshot, WorkspaceOptions


def store(tmp_path: Path) -> Settings:
    home = tmp_path / "Пользователь G03"
    home.mkdir(mode=0o700)
    return Settings(home, home / "my config")


def test_section_migration_is_read_only_and_preserves_comment_and_original(tmp_path):
    settings = store(tmp_path)
    assert settings.load("workspace").options == WorkspaceOptions()
    assert not settings.directory.exists()
    settings.directory.parent.mkdir(mode=0o700)
    settings.directory.mkdir(mode=0o700)
    legacy = settings.directory / "settings.toml"
    content = (
        b"# Preserve local notes\nschema_version = 1\nfirefox = true\n"
        b'optional_apps = ["telegram"]\nlanguage_profiles = ["cpp"]\n'
    )
    legacy.write_bytes(content)
    legacy.chmod(0o600)
    original = settings.load("workspace")
    assert original.options.apps == ("firefox", "telegram")
    assert original.options.languages == ("cpp",)
    assert list(settings.directory.iterdir()) == [legacy]
    current = settings.save("workspace", original.options, original.revision)
    assert legacy.read_bytes() == content
    assert current == settings.load("workspace")
    assert "# Preserve local notes" in (settings.directory / "workspace.toml").read_text()
    assert not (settings.directory / "base.toml").exists()


def test_existing_settings_atomic_exchange_keeps_original_inode(tmp_path):
    settings = store(tmp_path)
    original = settings.save("base", BaseOptions(timezone="UTC"), settings.load("base").revision)
    path = settings.directory / "base.toml"
    with path.open("ab") as editor:
        changed = settings.save("base", BaseOptions(timezone="Europe/Paris"), original.revision)
        editor.write(b"# Late editor note\n")
    assert settings.load("base") == changed
    assert "Late editor" not in path.read_text()
    assert (
        next(settings.directory.glob("base.toml.bak-*"))
        .read_text()
        .endswith("# Late editor note\n")
    )
    assert path.stat().st_mode & 0o777 == 0o600


def test_external_edit_or_new_legacy_source_demands_reload(tmp_path):
    settings = store(tmp_path)
    original = settings.load("base")
    settings.directory.parent.mkdir(mode=0o700)
    settings.directory.mkdir(mode=0o700)
    source = settings.directory / "system-intentions.toml"
    source.write_text('timezone = "UTC" # chosen by owner\n')
    source.chmod(0o600)
    with pytest.raises(ValueError, match="changed"):
        settings.save("base", original.options, original.revision)
    assert not (settings.directory / "base.toml").exists()
    loaded = settings.load("base")
    assert loaded.options.timezone == "UTC"
    result = settings.save("base", BaseOptions(timezone="Europe/Paris"), loaded.revision)
    path = settings.directory / "base.toml"
    assert "# chosen by owner" in path.read_text()
    path.write_text('timezone = "America/Chicago"\n')
    with pytest.raises(ValueError, match="changed"):
        settings.save("base", result.options, result.revision)
    assert path.read_text() == 'timezone = "America/Chicago"\n'


@pytest.mark.parametrize(
    "filename,data",
    [
        ("workspace.toml", "schema_version = 55"),
        ("settings.toml", "schema_version = true"),
        ("settings.toml", "stealth = true"),
        ("settings.toml", "firefox = 1"),
        ("settings.toml", "schema_version = 2\napps = 'codex'"),
    ],
)
def test_unknown_conflicting_schema_preserved(tmp_path, filename, data):
    settings = store(tmp_path)
    settings.directory.parent.mkdir(mode=0o700)
    settings.directory.mkdir(mode=0o700)
    path = settings.directory / filename
    path.write_text(data)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        settings.load("workspace")
    assert path.read_text() == data


def test_settings_paths_require_owned_directories_and_regular_files(tmp_path):
    settings = store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    settings.directory.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        settings.load("base")
    assert list(outside.iterdir()) == []
    settings.directory.parent.unlink()
    settings.directory.parent.mkdir(mode=0o700)
    settings.directory.mkdir(mode=0o700)
    file = settings.directory / "base.toml"
    file.symlink_to(tmp_path / "untrusted")
    with pytest.raises(OSError):
        settings.load("base")
    file.unlink()
    file.touch(0o600)
    file.chmod(0o666)
    with pytest.raises(ValueError, match="Unsafe"):
        settings.load("base")


def test_plan_snapshot_freezes_inputs_and_requires_privilege_scope():
    choices = {"apps": ["neovim"]}
    result = {
        "status": "ready",
        "actions": [
            {"kind": "user_file", "path": ".config/nvim/init.lua", "label": "Write editor profile"}
        ],
        "observed": {"needs_user": True, "needs_system": False, "uid": 1234},
    }
    plan = PlanSnapshot.capture("workspace", choices, result, "workspace.toml:absent")
    choices["apps"].clear()
    result["observed"]["uid"] = 1
    assert plan.payload()["settings"] == {"apps": ["neovim"]}
    assert plan.payload()["expected"]["observed"]["uid"] == 1234
    assert not plan.needs_password
    with pytest.raises(ValueError, match="scope"):
        PlanSnapshot.capture(
            "workspace",
            {},
            {"status": "ready", "actions": [{"kind": "editor_verify", "label": "Verify editor"}]},
            "absent",
        )
    with pytest.raises(ValidationError):
        BaseOptions(hyprland="true")
    assert BaseOptions(keyboard="de,fr", locale="de_DE.UTF-8").keyboard == "de,fr"
    with pytest.raises(ValidationError):
        BaseOptions(timezone="../../etc/shadow")


def test_recovery_preserves_permission_change_racing_the_exchange(tmp_path, monkeypatch):
    from setup_system.infrastructure import user_storage as storage

    settings = store(tmp_path)
    saved = settings.save("base", BaseOptions(timezone="UTC"), settings.load("base").revision)
    target = settings.directory / "base.toml"
    before = target.read_bytes()
    exchange = storage.exchange

    def chmod_before_publication(fd, backup, name):
        monkeypatch.setattr(storage, "exchange", exchange)
        target.chmod(0o666)
        exchange(fd, backup, name)

    monkeypatch.setattr(storage, "exchange", chmod_before_publication)
    with pytest.raises(ValueError):
        settings.save("base", BaseOptions(timezone="Europe/Paris"), saved.revision)
    assert target.read_bytes() == before
    assert target.stat().st_mode & 0o777 == 0o666


def test_legacy_settings_edit_during_migration_keeps_the_owner_version(tmp_path, monkeypatch):
    from setup_system.infrastructure import user_storage as storage

    settings = store(tmp_path)
    settings.directory.mkdir(parents=True)
    original = settings.directory / "system-intentions.toml"
    original.write_text('timezone = "UTC"\n')
    before = settings.load("base")
    publish = storage.rename_new

    def late_edit(*args, **kwargs):
        publish(*args, **kwargs)
        monkeypatch.setattr(storage, "rename_new", publish)
        original.write_text('timezone = "Europe/Berlin"\n')

    monkeypatch.setattr(storage, "rename_new", late_edit)
    with pytest.raises(ValueError, match="Concurrent"):
        settings.save("base", BaseOptions(timezone="Europe/Paris"), before.revision)
    assert original.read_text() == 'timezone = "Europe/Berlin"\n'
    assert not (settings.directory / "base.toml").exists()
    assert settings.load("base").options.timezone == "Europe/Berlin"


@pytest.mark.parametrize("section", ["base", "workspace"])
def test_packaged_examples_load_from_an_alternate_xdg_home(tmp_path, monkeypatch, section):
    home = tmp_path / "Private home"
    home.mkdir(mode=0o700)
    config = home / "Мои настройки"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    settings = Settings()
    assert settings.directory == config / "setup-system"
    settings.load(section)
    assert not config.exists()
    source = Path(__file__).resolve().parents[1] / "config" / section / "example.toml"
    settings.directory.mkdir(parents=True, mode=0o700)
    (settings.directory / f"{section}.toml").write_bytes(source.read_bytes())
    choices = settings.load(section).options
    if section == "base":
        assert choices.timezone == "Europe/Paris"
    else:
        assert choices.apps == ("firefox",) and choices.languages == ("python",)


def test_replaced_settings_inode_rejected_even_if_contents_match(tmp_path, monkeypatch):
    from setup_system.infrastructure import user_storage as storage

    settings = store(tmp_path)
    saved = settings.save("base", BaseOptions(timezone="UTC"), settings.load("base").revision)
    target = settings.directory / "base.toml"
    original = target.read_bytes()
    replacement = target.with_suffix(".tmp")
    replacement.write_bytes(original)
    replacement.chmod(0o600)
    expected_inode = replacement.stat().st_ino
    exchange = storage.exchange

    def replace_before_publication(fd, staged, name):
        monkeypatch.setattr(storage, "exchange", exchange)
        replacement.replace(target)
        exchange(fd, staged, name)

    monkeypatch.setattr(storage, "exchange", replace_before_publication)
    with pytest.raises(ValueError, match="Concurrent"):
        settings.save("base", BaseOptions(timezone="Europe/Paris"), saved.revision)
    assert target.read_bytes() == original and target.stat().st_ino == expected_inode


def test_reload_revision_detects_replacement_with_the_same_settings(tmp_path):
    settings = store(tmp_path)
    saved = settings.save("base", BaseOptions(timezone="UTC"), settings.load("base").revision)
    target = settings.directory / "base.toml"
    replacement = target.with_suffix(".tmp")
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(0o600)
    replacement.replace(target)
    with pytest.raises(ValueError, match="changed"):
        settings.save("base", BaseOptions(timezone="Europe/Paris"), saved.revision)
    assert settings.load("base").options.timezone == "UTC"
