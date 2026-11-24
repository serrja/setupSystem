"""Uncoordinated administrator edits survive publication of reviewed system files."""

import hashlib
import os

import pytest

from setup_system.infrastructure import firefox_policy, system_publication
from setup_system.infrastructure.system_publication import observe_file, publish_file, save_backup


@pytest.mark.parametrize("kind", ["system", "firefox"])
def test_edit_after_backup_survives_target_publication(tmp_path, monkeypatch, kind):
    path = (
        tmp_path / "etc" / ("system.toml" if kind == "system" else "firefox/policies/policies.json")
    )
    path.parent.mkdir(mode=0o755, parents=True)
    if kind == "firefox":
        path.parent.parent.chmod(0o755)
        path.parent.chmod(0o755)
    backups = tmp_path / "backups"
    if kind == "system":
        initial = b'keyboard = "us"\n'
        path.write_bytes(initial)
        path.chmod(0o644)
        observed = observe_file(path)
        assert observed is not None

        def apply():
            save_backup(backups / (observed.digest + ".toml"), observed)
            publish_file(
                path, b'keyboard = "us,ru"\n', observed, mode=observed.mode, parent_mode=0o755
            )
    else:
        firefox_policy.apply(firefox_policy.inspect(path), backups=backups)
        initial = path.read_bytes().replace(b"/uBlock0@raymondhill.net/", b"/ublock-origin/")
        path.write_bytes(initial)
        plan = firefox_policy.inspect(path)

        def apply():
            firefox_policy.apply(plan, backups=backups)

    saved = backups / (hashlib.sha256(initial).hexdigest() + path.suffix)
    move = system_publication.move_new
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"Later administrator edit\n")
    replacement.chmod(0o600)
    identity = replacement.stat().st_ino

    def concurrent(source, target, **options):
        if source == path:
            assert saved.read_bytes() == initial
            replacement.replace(path)
        return move(source, target, **options)

    monkeypatch.setattr(system_publication, "move_new", concurrent)
    with pytest.raises(ValueError, match="changed"):
        apply()
    assert path.read_bytes() == b"Later administrator edit\n"
    assert path.stat().st_ino == identity and path.stat().st_mode & 0o777 == 0o600
    assert saved.read_bytes() == initial


def test_live_descriptor_survives_successful_settings_publish(tmp_path):
    path = tmp_path / "system.toml"
    initial = b"schema_version=1\n"
    path.write_bytes(initial)
    path.chmod(0o640)
    original = path.stat()
    with path.open("ab", buffering=0) as edit:
        observed = observe_file(path)
        assert observed is not None
        publish_file(path, b'keyboard = "us"\n', observed, mode=observed.mode, parent_mode=0o755)
        edit.write(b"# Later edit from an open file descriptor\n")
        os.fsync(edit.fileno())
    (retained,) = path.parent.glob(".setup-system-system.toml-*/original")
    assert retained.stat().st_ino == original.st_ino
    assert retained.read_bytes().endswith(b"file descriptor\n")
    assert (path.stat().st_uid, path.stat().st_gid, path.stat().st_mode) == (
        original.st_uid,
        original.st_gid,
        original.st_mode,
    )
    assert b'keyboard = "us"' in path.read_bytes()


@pytest.mark.parametrize("existed", [False, True])
def test_destination_appearing_during_publish_preserves_both_versions(
    tmp_path, monkeypatch, existed
):
    path = tmp_path / "system.toml"
    initial = None
    if existed:
        path.write_bytes(b"original state")
        path.chmod(0o644)
        initial = path.stat()
    move = system_publication.move_new

    def concurrent(source, target, **options):
        if source.name == "prepared" and target == path:
            path.write_bytes(b"Another administrator's new settings")
        return move(source, target, **options)

    monkeypatch.setattr(system_publication, "move_new", concurrent)
    with pytest.raises(ValueError, match=r"conflict|appeared"):
        publish_file(path, b"new state", observe_file(path), mode=0o644, parent_mode=0o755)
    if initial is not None:
        (retained,) = path.parent.glob(".setup-system-system.toml-*/original")
        assert (
            retained.stat().st_ino == initial.st_ino and retained.read_bytes() == b"original state"
        )
    assert path.read_bytes() == b"Another administrator's new settings"


def test_late_open_descriptor_edit_is_detected_before_completion(tmp_path, monkeypatch):
    path = tmp_path / "system.toml"
    path.write_bytes(b"initial")
    path.chmod(0o644)
    old = path.stat().st_ino
    move = system_publication.move_new
    with path.open("ab", buffering=0) as writer:

        def late_edit(source, target, **kwargs):
            if source.name == "prepared" and target == path:
                writer.write(b" later")
                os.fsync(writer.fileno())
            return move(source, target, **kwargs)

        monkeypatch.setattr(system_publication, "move_new", late_edit)
        with pytest.raises(ValueError, match="changed"):
            publish_file(path, b"new state", observe_file(path), mode=0o644, parent_mode=0o755)
    assert path.read_bytes() == b"initial later" and path.stat().st_ino == old


def test_private_umask_does_not_break_retention(tmp_path):
    path = tmp_path / "etc/system.toml"
    mask = os.umask(0o777)
    try:
        publish_file(path, b"original", None, mode=0o644, parent_mode=0o755)
        current = observe_file(path)
        assert current is not None
        backup = tmp_path / "history" / (current.digest + ".toml")
        save_backup(backup, current)
        publish_file(path, b"updated", current, mode=current.mode, parent_mode=0o755)
    finally:
        os.umask(mask)
    (retained,) = path.parent.glob(".setup-system-system.toml-*/original")
    assert retained.parent.stat().st_mode & 0o777 == 0o700
    assert backup.stat().st_mode & 0o777 == 0o600


def test_settings_preserve_the_administrators_posix_access_acl(tmp_path):
    import struct

    path = tmp_path / "system.toml"
    path.write_bytes(b"initial")
    undefined = 0xFFFFFFFF
    # Linux POSIX ACL xattr version 2 with a named user entry.
    records = [
        (1, 6, undefined),
        (2, 4, os.getuid()),
        (4, 0, undefined),
        (16, 4, undefined),
        (32, 0, undefined),
    ]
    encoded = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in records)
    os.setxattr(path, "system.posix_acl_access", encoded)
    current = observe_file(path)
    assert current is not None
    publish_file(path, b"updated", current, mode=current.mode, parent_mode=0o755)
    assert os.getxattr(path, "system.posix_acl_access") == encoded
