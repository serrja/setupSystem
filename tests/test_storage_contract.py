"""Exercise backup/recovery using synthetic files in an injected home only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from setup_system import storage


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _class: home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    return home


def test_atomic_write_modes_idempotence_and_directory_refusal(sandbox: Path) -> None:
    path = sandbox / "config/owned.txt"
    assert storage.atomic_write(path, b"first", 0o640) == "changed"
    assert storage.atomic_write(path, b"first", 0o600) == "unchanged"
    assert storage.atomic_write(path, b"second") == "changed"
    assert path.stat().st_mode & 0o777 == 0o640
    assert storage.atomic_write(path, b"third", 0o600, preserve_existing_mode=False) == "changed"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="regular file"):
        storage.atomic_write(path.parent, b"invalid")
    assert list(path.parent.iterdir()) == [path]


def test_atomic_write_rejects_escape_and_symlink(sandbox: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = sandbox / "link"
    link.symlink_to(outside, target_is_directory=True)
    for path in (outside / "file", link / "file"):
        with pytest.raises(ValueError, match=r"inside|symlink"):
            storage.atomic_write(path, b"no-write")
    assert list(outside.iterdir()) == []


def test_backup_dry_run_conflict_restore_and_previous_copy(sandbox: Path) -> None:
    path = sandbox / "config/user.txt"
    storage.atomic_write(path, b"original", 0o640)
    backup = storage.new_backup([path, path, sandbox / "absent"])
    assert backup.stat().st_mode & 0o777 == 0o700
    manifest = json.loads((backup / "manifest.json").read_text())
    assert len(manifest["items"]) == 1
    assert (backup / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert storage.restore(backup.name) == [(str(path), "unchanged")]
    path.write_bytes(b"changed-user-data")
    assert storage.restore(backup.name, apply=True) == [(str(path), "conflict")]
    assert path.read_bytes() == b"changed-user-data"
    assert storage.restore(backup.name, apply=True, replace_current=True) == [
        (str(path), "restored")
    ]
    assert path.read_bytes() == b"original" and path.stat().st_mode & 0o777 == 0o640
    other = [item for item in backup.parent.iterdir() if item != backup]
    assert len(other) == 1
    assert (other[0] / "files/config/user.txt").read_bytes() == b"changed-user-data"
    path.unlink()
    assert storage.restore(backup.name) == [(str(path), "would-create")]
    assert not path.exists()
    assert storage.restore(backup.name, apply=True) == [(str(path), "restored")]


@pytest.mark.parametrize("problem", ["hash", "escape", "symlink", "id", "happ", "target-directory"])
def test_restore_rejects_tampered_input_before_writing(sandbox: Path, problem: str) -> None:
    path = sandbox / "user.txt"
    storage.atomic_write(path, b"protected")
    backup = storage.new_backup([path])
    file = backup / "manifest.json"
    manifest = json.loads(file.read_text())
    item = manifest["items"][0]
    if problem == "hash":
        (backup / item["backup"]).write_bytes(b"corrupt")
    elif problem == "escape":
        item["home_relative"] = "../outside"
    elif problem == "symlink":
        content = backup / item["backup"]
        content.unlink()
        content.symlink_to(path)
    elif problem == "id":
        manifest["run_id"] = "wrong"
    elif problem == "happ":
        manifest["application"] = "happ"
    else:
        path.unlink()
        path.mkdir()
    file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        storage.restore(backup.name, apply=True, replace_current=True)
    assert path.is_dir() if problem == "target-directory" else path.read_bytes() == b"protected"


@pytest.mark.parametrize("backup_id", ["../outside", "", "20260101T000000Z-not-hex"])
def test_restore_invalid_id_is_rejected(sandbox: Path, backup_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid backup ID"):
        storage.restore(backup_id)
    assert list(sandbox.iterdir()) == []


@pytest.mark.parametrize(
    "original", ["# >>> setupSystem >>>", "# <<< setupSystem <<<", "# >>> setupSystem >>>" * 2]
)
def test_managed_block_rejects_malformed_markers(original: str) -> None:
    with pytest.raises(ValueError, match="Malformed"):
        storage.managed_block(original, "owned")


def test_managed_block_preserves_user_content_and_removes_only_owned() -> None:
    original = "user-before\n# >>> setupSystem >>>\nold\n# <<< setupSystem <<<\nuser-after\n"
    changed = storage.managed_block(original, "new")
    assert "user-before" in changed and "user-after" in changed and "old" not in changed
    assert changed.count("# >>> setupSystem >>>") == 1
    removed = storage.managed_block(changed, None)
    assert removed == "user-before\nuser-after\n"
