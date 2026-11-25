"""Profile compare-and-swap tests preserve content and concurrent editors."""

import pytest

from setup_system.configuration import Settings
from setup_system.configuration.store import load_values, save_values


def store(tmp_path):
    home = tmp_path / "synthetic home"
    home.mkdir(mode=0o700)
    return Settings(home, home / "my config")


def save(settings, value, revision):
    return save_values(settings, "sample", "default", {"timezone": value}, 1, revision)


def test_existing_settings_atomic_exchange_keeps_original_inode(tmp_path):
    settings = store(tmp_path)
    original = save(settings, "UTC", load_values(settings, "sample", "default").revision)
    path = settings.directory / "plugins/sample/default.toml"
    with path.open("ab") as editor:
        changed = save(settings, "Europe/Paris", original.revision)
        editor.write(b"# Late editor note\n")
    assert load_values(settings, "sample", "default") == changed
    assert "Late editor" not in path.read_text()
    assert next(path.parent.glob("default.toml.bak-*")).read_text().endswith("# Late editor note\n")
    assert path.stat().st_mode & 0o777 == 0o600


def test_recovery_preserves_permission_change_racing_the_exchange(tmp_path, monkeypatch):
    from setup_system.infrastructure import user_storage as storage

    settings = store(tmp_path)
    saved = save(settings, "UTC", load_values(settings, "sample", "default").revision)
    target = settings.directory / "plugins/sample/default.toml"
    before = target.read_bytes()
    exchange = storage.exchange

    def chmod_before_publication(fd, backup, name):
        monkeypatch.setattr(storage, "exchange", exchange)
        target.chmod(0o666)
        exchange(fd, backup, name)

    monkeypatch.setattr(storage, "exchange", chmod_before_publication)
    with pytest.raises(ValueError):
        save(settings, "Europe/Paris", saved.revision)
    assert target.read_bytes() == before
    assert target.stat().st_mode & 0o777 == 0o666


def test_replaced_settings_inode_rejected_even_if_contents_match(tmp_path, monkeypatch):
    from setup_system.infrastructure import user_storage as storage

    settings = store(tmp_path)
    saved = save(settings, "UTC", load_values(settings, "sample", "default").revision)
    target = settings.directory / "plugins/sample/default.toml"
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
        save(settings, "Europe/Paris", saved.revision)
    assert target.read_bytes() == original and target.stat().st_ino == expected_inode


def test_reload_revision_detects_replacement_with_the_same_settings(tmp_path):
    settings = store(tmp_path)
    saved = save(settings, "UTC", load_values(settings, "sample", "default").revision)
    target = settings.directory / "plugins/sample/default.toml"
    replacement = target.with_suffix(".tmp")
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(0o600)
    replacement.replace(target)
    with pytest.raises(ValueError, match="changed"):
        save(settings, "Europe/Paris", saved.revision)
    assert load_values(settings, "sample", "default").values["timezone"] == "UTC"
