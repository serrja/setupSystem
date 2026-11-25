"""Retain directory permissions and protect existing Firefox policy."""

import pytest
from setup_plugin_firefox import policy

OPTIONS = policy.Settings(install_ublock=True)


def test_new_firefox_policy_is_readable_through_each_created_directory(tmp_path, monkeypatch):
    path = tmp_path / "etc/firefox/policies/policies.json"
    policy.publish(policy.inspect(path, OPTIONS), OPTIONS, backups=tmp_path / "backup")
    for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert directory.stat().st_mode & 0o005 == 0o005
    assert path.stat().st_mode & 0o004
    assert "downloads/latest/uBlock0@raymondhill.net/latest.xpi" in path.read_text()
    # Keep unrelated administrator-managed policies when merging the chosen settings.
    path.write_text('{"policies":{"DisableFormHistory":true}}')
    policy.publish(policy.inspect(path, OPTIONS), OPTIONS, backups=tmp_path / "backup")
    assert '"DisableFormHistory"' in path.read_text()


def test_known_private_firefox_policy_migrates_without_exposing_other_files(tmp_path, monkeypatch):
    path = tmp_path / "etc/firefox/policies/policies.json"
    backups = tmp_path / "backups"
    policy.publish(policy.inspect(path, OPTIONS), OPTIONS, backups=backups)
    legacy = path.read_bytes().replace(
        b"/uBlock0@raymondhill.net/latest.xpi", b"/ublock-origin/latest.xpi"
    )
    path.write_bytes(legacy)
    for directory in (path.parent.parent, path.parent):
        directory.chmod(0o700)
    other = path.parent / "admin.conf"
    other.touch()
    with pytest.raises(ValueError, match="unknown contents"):
        policy.publish(policy.inspect(path, OPTIONS), OPTIONS, backups=backups)
    assert not (path.parent.parent.stat().st_mode & 0o077)
    other.unlink()
    policy.publish(policy.inspect(path, OPTIONS), OPTIONS, backups=backups)
    assert all(directory.stat().st_mode & 0o001 for directory in (path.parent.parent, path.parent))
    assert next(backups.iterdir()).read_bytes() == legacy
