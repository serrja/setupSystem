"""Exercise backup/recovery using synthetic files in an injected home only."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from historical_receipts import historical_backup

from setup_system.domain.managed_text import managed_block
from setup_system.file_tasks import restore
from setup_system.infrastructure import user_storage


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _class: home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    return home


def test_atomic_write_modes_idempotence_and_directory_refusal(sandbox: Path) -> None:
    path = sandbox / "config/owned.txt"
    assert user_storage.atomic_write(path, b"first", 0o640) == "changed"
    assert user_storage.atomic_write(path, b"first", 0o600) == "unchanged"
    assert user_storage.atomic_write(path, b"second") == "changed"
    assert path.stat().st_mode & 0o777 == 0o640
    assert (
        user_storage.atomic_write(path, b"third", 0o600, preserve_existing_mode=False) == "changed"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="regular file"):
        user_storage.atomic_write(path.parent, b"invalid")
    assert list(path.parent.iterdir()) == [path]


def test_atomic_write_rejects_escape_and_symlink(sandbox: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = sandbox / "link"
    link.symlink_to(outside, target_is_directory=True)
    for path in (outside / "file", link / "file"):
        with pytest.raises(ValueError, match=r"inside|symlink"):
            user_storage.atomic_write(path, b"no-write")
    assert list(outside.iterdir()) == []


def test_backup_preview_conflict_then_restore_missing_original(sandbox: Path) -> None:
    path = sandbox / "config/user.txt"
    user_storage.atomic_write(path, b"original", 0o640)
    backup = historical_backup([path, path, sandbox / "absent"])
    assert backup.stat().st_mode & 0o777 == 0o700
    manifest = json.loads((backup / "manifest.json").read_text())
    assert len(manifest["items"]) == 1
    assert (backup / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert restore(backup.name) == [(str(path), "unchanged")]
    path.write_bytes(b"changed-user-data")
    assert restore(backup.name) == [(str(path), "conflict")]
    with pytest.raises(ValueError, match="later edit"):
        restore(backup.name, apply=True)
    assert path.read_bytes() == b"changed-user-data"
    path.unlink()
    assert restore(backup.name) == [(str(path), "would-create")]
    assert not path.exists()
    assert restore(backup.name, apply=True) == [(str(path), "restored")]


@pytest.mark.parametrize(
    "problem",
    ["hash", "escape", "symlink", "id", "happ", "target-directory", "future-field", "duplicate"],
)
def test_restore_rejects_tampered_input_before_writing(sandbox: Path, problem: str) -> None:
    path = sandbox / "user.txt"
    user_storage.atomic_write(path, b"protected")
    backup = historical_backup([path])
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
    elif problem == "future-field":
        item["transferOwnership"] = True
    elif problem == "duplicate":
        manifest["items"].append(dict(item))
    else:
        path.unlink()
        path.mkdir()
    file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore(backup.name, apply=True)
    assert path.is_dir() if problem == "target-directory" else path.read_bytes() == b"protected"


@pytest.mark.parametrize("backup_id", ["../outside", "", "20260101T000000Z-not-hex"])
def test_restore_invalid_id_is_rejected(sandbox: Path, backup_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid backup ID"):
        restore(backup_id)
    assert list(sandbox.iterdir()) == []


@pytest.mark.parametrize(
    "original", ["# >>> setupSystem >>>", "# <<< setupSystem <<<", "# >>> setupSystem >>>" * 2]
)
def test_managed_block_rejects_malformed_markers(original: str) -> None:
    with pytest.raises(ValueError, match="Malformed"):
        managed_block(original, "owned")


def test_managed_block_preserves_user_content_and_removes_only_owned() -> None:
    original = "user-before\n# >>> setupSystem >>>\nold\n# <<< setupSystem <<<\nuser-after\n"
    changed = managed_block(original, "new")
    assert "user-before" in changed and "user-after" in changed and "old" not in changed
    assert changed.count("# >>> setupSystem >>>") == 1
    removed = managed_block(changed, None)
    assert removed == "user-before\nuser-after\n"


def test_apply_and_restore_create_private_intermediates_under_shared_umask(tmp_path, monkeypatch):
    from setup_system.infrastructure.managed_files import apply_files

    home = tmp_path / "synthetic home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "nested/state"))
    target = home / "working/settings/choice.conf"
    previous = os.umask(0o002)
    try:
        create = apply_files({target: b"first"})
        changed = apply_files({target: b"second"})
        assert (changed / "files/working/settings/choice.conf").read_bytes() == b"first"
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o700 for p in home.rglob("*") if p.is_dir())
        restore(changed.name, apply=True)
        assert target.read_bytes() == b"first"
        restore(create.name, apply=True)
        assert not target.exists()
        assert os.umask(0o002) == 0o002
    finally:
        os.umask(previous)


def test_existing_shared_parent_is_preserved_before_an_atomic_write(tmp_path, monkeypatch):
    from setup_system.infrastructure.managed_files import apply_files

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    shared = home / "shared"
    shared.mkdir()
    shared.chmod(0o775)
    target = shared / "nested/record.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(shared / "state"))
    original = os.umask(0o002)
    try:
        with pytest.raises(ValueError, match="permissions"):
            user_storage.atomic_write(target, b"private")
        with pytest.raises(ValueError, match="permissions"):
            apply_files({home / "absent": b"private"})
    finally:
        os.umask(original)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o775
    assert not list(shared.iterdir()) and not (home / "absent").exists()


def test_historical_restore_creates_all_missing_parents_privately(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    target = home / "old/config/entry"
    original = os.umask(0o077)
    try:
        target.parent.mkdir(parents=True)
        target.write_bytes(b"previous")
        backup = historical_backup([target])
        target.unlink()
        target.parent.rmdir()
        target.parent.parent.rmdir()
        os.umask(0o002)
        assert restore(backup.name, apply=True) == [(str(target), "restored")]
        assert target.read_bytes() == b"previous"
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.parent.parent.stat().st_mode) == 0o700
    finally:
        os.umask(original)


def test_confirmed_manifest_restore_recovers_only_missing_files(sandbox, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from setup_system.file_tasks import cli
    from setup_system.models import TaskResult

    original = sandbox / "file"
    original.write_bytes(b"saved")
    backup = historical_backup([original])
    original.unlink()
    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    monkeypatch.setenv("SETUP_SYSTEM_REQUEST", str(request))
    monkeypatch.setenv("SETUP_SYSTEM_RESULT", str(result))

    def invoke(target, expected=None):
        request.write_text(
            json.dumps(dict(schema=1, settings={"backup_id": backup.name}, expected=expected))
        )
        response = CliRunner().invoke(cli, [target])
        assert response.exit_code == 0, response.exception
        assert response.stdout == ""
        return TaskResult.model_validate_json(result.read_bytes())

    preview = invoke("restore-plan")
    assert [action.operation for action in preview.actions] == ["would-create"]
    consent = preview.model_dump(mode="json", exclude_unset=True)
    original.write_bytes(b"changed after preview")
    request.write_text(
        json.dumps(dict(schema=1, settings={"backup_id": backup.name}, expected=consent))
    )
    assert CliRunner().invoke(cli, ["restore-apply"]).exit_code != 0
    assert original.read_bytes() == b"changed after preview"
    original.unlink()
    assert invoke("restore-apply", consent).status == "complete"
    assert original.read_bytes() == b"saved"
    assert invoke("restore-plan").status == "no_changes"
