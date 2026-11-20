"""Run the real local Ansible role only against a synthetic HOME/XDG tree."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from setup_system.infrastructure.user_files import apply_files


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    return home


def test_real_role_writes_and_repeats_without_another_backup(home: Path) -> None:
    target = home / "config/tool/settings.ini"
    first = apply_files({target: b"color=green\n"})
    assert target.read_bytes() == b"color=green\n"
    assert (first / "operation.json").is_file()
    backups = list((home / "state/setup-system/backups").iterdir())
    apply_files({target: b"color=green\n"})
    assert list((home / "state/setup-system/backups").iterdir()) == backups
    target.write_text("owner change\n")
    with pytest.raises(ValueError, match="edited"):
        apply_files({target: b"color=teal\n"})
    assert target.read_text() == "owner change\n"


def test_partial_runner_failure_records_only_completed_files_and_can_retry(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ansible_runner

    first = home / "config/first"
    second = home / "config/second"

    def partial(**kwargs):
        item = kwargs["extravars"]["user_files_managed_files"][0]
        Path(item["destination"]).write_bytes(Path(item["source"]).read_bytes())
        Path(item["destination"]).chmod(int(item["mode"], 8))
        kwargs["event_handler"](
            {"event": "runner_item_on_ok", "event_data": {"res": {"dest": item["destination"]}}}
        )
        return SimpleNamespace(rc=1)

    original = ansible_runner.run
    monkeypatch.setattr(ansible_runner, "run", partial)
    with pytest.raises(RuntimeError, match="partially"):
        apply_files({first: b"first", second: b"second"})
    receipt = json.loads((home / "state/setup-system/workstation.json").read_text())
    assert str(first) in receipt["files"] and str(second) not in receipt["files"]
    monkeypatch.setattr(ansible_runner, "run", original)
    apply_files({first: b"first", second: b"second"})
    assert first.read_bytes() == b"first" and second.read_bytes() == b"second"


def test_user_role_never_follows_symlink_target(home: Path) -> None:
    outside = home / "outside"
    outside.mkdir()
    target = home / "config"
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        apply_files({target / "managed": b"forbidden"})
    assert not list(outside.iterdir())


def test_workspace_restore_reverts_changed_files_and_removes_created_paths(home: Path) -> None:
    from setup_system.application.tools import ToolRequest, execute

    existing = home / "config/tool/first"
    created = home / "config/nested/new"
    apply_files({existing: b"first"})
    changed = apply_files({existing: b"second", created: b"new"})
    request = ToolRequest(action="restore", backup_id=changed.name)
    execute(Path(__file__).resolve().parents[1], request, confirmed=True)
    assert existing.read_bytes() == b"first"
    assert not created.exists()
    assert not created.parent.exists()
    execute(Path(__file__).resolve().parents[1], request, confirmed=True)
    assert existing.read_bytes() == b"first"


def test_interrupted_runner_keeps_recovery_and_only_verified_writes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ansible_runner

    from setup_system.infrastructure.file_receipts import list_operations
    from setup_system.storage import restore

    selected = home / "config/tool/selected"
    pending = home / "missing/tool/pending"

    def interrupt(**kwargs):
        row = kwargs["extravars"]["user_files_managed_files"][0]
        selected.write_bytes(Path(row["source"]).read_bytes())
        selected.chmod(int(row["mode"], 8))
        kwargs["event_handler"](
            {"event": "runner_item_on_ok", "event_data": {"res": {"dest": str(selected)}}}
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(ansible_runner, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        apply_files({selected: b"done", pending: b"pending"})
    ((operation, status),) = list_operations()
    assert status == "partial" and not pending.exists()
    pending.write_bytes(b"later file")
    with pytest.raises(ValueError, match="later edit"):
        restore(operation, apply=True)
    assert selected.read_bytes() == b"done" and pending.read_bytes() == b"later file"
    pending.unlink()
    restore(operation, apply=True)
    assert not selected.exists() and not pending.parent.exists()


def test_runner_success_without_expected_output_fails_readback(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    import ansible_runner

    from setup_system.infrastructure.file_receipts import list_operations

    monkeypatch.setattr(ansible_runner, "run", lambda **kwargs: SimpleNamespace(rc=0))
    with pytest.raises(RuntimeError, match="failed readback"):
        apply_files({home / "file": b"expected"})
    assert list_operations()[0][1] == "failed"


def test_legacy_journal_migration_backs_up_originals_and_preserves_modes(home: Path) -> None:
    from setup_system.infrastructure.file_receipts import digest
    from setup_system.storage import new_backup, restore, state_root

    original = home / "tool/owned"
    original.parent.mkdir()
    original.write_bytes(b"before")
    original.chmod(0o640)
    index = state_root() / "workstation.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({str(original): digest(b"before")}))
    legacy = new_backup([original, index])
    created = home / "tool/new"
    original.write_bytes(b"applied")
    created.write_bytes(b"created")
    created.chmod(0o644)
    original.chmod(0o644)
    applied = {str(original): digest(b"applied"), str(created): digest(b"created")}
    index.write_text(json.dumps(applied))
    op = legacy / "operation.json"
    op.write_text(json.dumps({"status": "complete", "after": applied, "completed": applied}))
    previous_op = op.read_bytes()
    previous_index = index.read_bytes()
    preview = restore(legacy.name)
    assert preview == [(str(original), "would-restore"), (str(created), "would-remove")]
    assert op.read_bytes() == previous_op and index.read_bytes() == previous_index
    restore(legacy.name, apply=True)
    assert op.with_name("operation.v0.json").read_bytes() == previous_op
    assert index.with_name("workstation.v0.json").read_bytes() == previous_index
    assert json.loads(op.read_text())["schema_version"] == 1
    assert original.read_bytes() == b"before" and original.stat().st_mode & 0o777 == 0o640
    assert not created.exists()
    assert json.loads(index.read_text())["files"] == {str(original): digest(b"before")}


def test_unknown_receipt_schema_is_a_read_only_conflict(home: Path) -> None:
    from setup_system.storage import new_backup, restore

    target = home / "original"
    target.write_bytes(b"before")
    backup = new_backup([target])
    target.write_bytes(b"after")
    journal = backup / "operation.json"
    journal.write_text('{"schema_version":21}')
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        restore(backup.name, apply=True)
    assert before == {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}


def test_restore_refuses_later_mode_change_without_touching_a_sibling(home: Path) -> None:
    from setup_system.storage import restore

    one, two = home / "one", home / "two"
    op = apply_files({one: b"first", two: b"second"})
    two.chmod(0o600)
    with pytest.raises(ValueError, match="later edit"):
        restore(op.name, apply=True)
    assert one.read_bytes() == b"first" and two.stat().st_mode & 0o777 == 0o600


def test_concurrent_restore_edit_is_returned_without_overwriting(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from setup_system.storage import restore

    target = home / "tool"
    op = apply_files({target: b"managed"})
    from setup_system.infrastructure import file_receipts

    original = file_receipts._move_new

    def concurrent_edit(source, destination):
        if source == target:
            target.write_bytes(b"owner edited during restore")
        return original(source, destination)

    monkeypatch.setattr(file_receipts, "_move_new", concurrent_edit)
    with pytest.raises(ValueError, match="later edit"):
        restore(op.name, apply=True)
    assert target.read_bytes() == b"owner edited during restore"


def test_interrupted_restore_resumes_from_private_staging(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.infrastructure import file_receipts
    from setup_system.storage import restore

    target = home / "tool"
    apply_files({target: b"first"})
    op = apply_files({target: b"second"})
    original = file_receipts._publish_original

    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(file_receipts, "_publish_original", interrupt)
    with pytest.raises(KeyboardInterrupt):
        restore(op.name, apply=True)
    monkeypatch.setattr(file_receipts, "_publish_original", original)
    assert restore(op.name) == [(str(target), "would-restore")]
    restore(op.name, apply=True)
    assert target.read_bytes() == b"first"
    assert not list(home.glob(".setup-system-restore-*"))


def test_legacy_desktop_preparation_requires_committed_index(home: Path) -> None:
    from setup_system.infrastructure.file_receipts import digest
    from setup_system.storage import new_backup, restore, state_root

    managed = home / "font.conf"
    index = state_root() / "workstation.json"
    backup = new_backup([])
    managed.write_bytes(b"applied")
    managed.chmod(0o644)
    checksum = digest(managed.read_bytes())
    expected_index = json.dumps({str(managed): checksum, "__backup__": str(backup)}).encode()
    payload = {
        "profile": "fonts",
        "files": [
            {"path": str(managed), "created": True, "after": checksum},
            {"path": str(index), "created": True, "after": digest(expected_index)},
        ],
        "settings": [],
    }
    (backup / "workstation.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="ownership"):
        restore(backup.name, apply=True)
    assert managed.read_bytes() == b"applied"
    index.write_bytes(expected_index)
    restore(backup.name, apply=True)
    assert not managed.exists()


def test_legacy_git_before_hash_must_match_backup(home: Path) -> None:
    from setup_system.infrastructure.file_receipts import digest
    from setup_system.storage import new_backup, restore

    config = home / ".gitconfig"
    config.write_bytes(b"old")
    backup = new_backup([config])
    config.write_bytes(b"new")
    payload = {
        "application": "git-profile",
        "status": "complete",
        "files": [{"path": str(config), "before": digest(b"wrong"), "after": digest(b"new")}],
    }
    (backup / "git-profile.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="original backup"):
        restore(backup.name, apply=True)
    assert config.read_bytes() == b"new"


@pytest.mark.parametrize("existed", [True, False])
def test_concurrent_edit_between_inspection_and_role_publication(
    home: Path, monkeypatch: pytest.MonkeyPatch, existed: bool
) -> None:
    import ansible_runner

    from setup_system.infrastructure.file_receipts import digest

    target = home / "ordinary-editor.txt"
    if existed:
        target.write_bytes(b"confirmed owner input")
    original = ansible_runner.run
    injected = False

    def during_publication(**kwargs):
        callback = kwargs["event_handler"]

        def on_event(event):
            nonlocal injected
            task = event.get("event_data", {}).get("task", "")
            if event.get("event") == "playbook_on_task_start" and (
                "Write validated" in task or "Publish validated" in task
            ):
                target.write_bytes(b"owner edit during publication")
                injected = True
            return callback(event)

        kwargs["event_handler"] = on_event
        return original(**kwargs)

    monkeypatch.setattr(ansible_runner, "run", during_publication)
    with pytest.raises((RuntimeError, ValueError)):
        apply_files(
            {target: b"selected content"},
            expected={target: digest(b"confirmed owner input") if existed else None},
        )
    assert injected
    assert target.read_bytes() == b"owner edit during publication"


def test_interrupted_displacement_is_recovered_before_another_apply(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.infrastructure import file_receipts, user_files
    from setup_system.storage import restore

    target = home / "tool"
    target.write_bytes(b"owner's original")
    before = file_receipts.digest(target.read_bytes())
    original = file_receipts._move_new

    def interrupt(source, destination):
        original(source, destination)
        if source == target:
            raise KeyboardInterrupt

    def run_role(rows, _desired, callback):
        for row in rows:
            callback(file_receipts.publish_prepared(row))
        return 0

    monkeypatch.setattr(file_receipts, "_move_new", interrupt)
    monkeypatch.setattr(user_files, "_run_role", run_role)
    with pytest.raises(KeyboardInterrupt):
        apply_files({target: b"selected"}, expected={target: before})
    assert not target.exists()
    ((operation, status),) = file_receipts.list_operations()
    assert status == "partial"
    unrelated = home / "unrelated"
    apply_files({unrelated: b"independent"})
    assert unrelated.read_bytes() == b"independent"
    with pytest.raises(ValueError, match="Restore interrupted file publication"):
        apply_files({target: b"selected"})
    assert not target.exists()
    assert restore(operation) == [(str(target), "would-return-displaced")]
    restore(operation, apply=True)
    assert target.read_bytes() == b"owner's original"


def test_owner_recreates_target_while_prior_inode_is_held(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.diagnostics import PartialApplyError
    from setup_system.infrastructure import file_receipts, user_files
    from setup_system.storage import restore

    target = home / "config.ini"
    target.write_bytes(b"prior file")
    move = file_receipts._move_new

    def racing_move(source, dest):
        move(source, dest)
        if source == target:
            target.write_bytes(b"owner's replacement")

    def run_role(rows, _desired, callback):
        for row in rows:
            callback(file_receipts.publish_prepared(row))
        return 0

    monkeypatch.setattr(file_receipts, "_move_new", racing_move)
    monkeypatch.setattr(user_files, "_run_role", run_role)
    with pytest.raises(PartialApplyError):
        apply_files({target: b"wanted"}, expected={target: file_receipts.digest(b"prior file")})
    assert target.read_bytes() == b"owner's replacement"
    ((operation, _),) = file_receipts.list_operations()
    op = file_receipts.load_operation(file_receipts.operation_folder(operation))
    retained = file_receipts.apply_stage(op.files[0], target) / "original"
    assert retained.read_bytes() == b"prior file"
    with pytest.raises(ValueError, match="later edit"):
        restore(operation, apply=True)
    assert target.read_bytes() == b"owner's replacement" and retained.is_file()


def test_open_writer_on_displaced_inode_keeps_its_late_edit(home: Path) -> None:
    from setup_system.infrastructure import file_receipts
    from setup_system.storage import restore

    target = home / "existing"
    target.write_bytes(b"checked")
    with target.open("wb") as editor:
        editor.write(b"checked")
        editor.flush()
        operation = apply_files(
            {target: b"configured"}, expected={target: file_receipts.digest(b"checked")}
        )
        editor.seek(0)
        editor.write(b"editor saved later")
    row = file_receipts.load_operation(operation).files[0]
    retained = file_receipts.apply_stage(row, target) / "original"
    assert target.read_bytes() == b"configured"
    assert retained.read_bytes() == b"editor saved later"
    with pytest.raises(ValueError, match="review displaced"):
        restore(operation.name, apply=True)
    assert retained.read_bytes() == b"editor saved later"


@pytest.mark.parametrize("separate", [False, True])
@pytest.mark.parametrize("existed", [False, True])
def test_restore_keeps_open_writer_on_the_removed_installation(
    home: Path, monkeypatch: pytest.MonkeyPatch, separate: bool, existed: bool
) -> None:
    from setup_system.infrastructure import file_receipts, user_files
    from setup_system.storage import restore

    if separate:

        def separate_filesystems(left, right):
            return left.is_relative_to(home / "state") == right.is_relative_to(home / "state")

        monkeypatch.setattr(user_files, "same_filesystem", separate_filesystems)
        monkeypatch.setattr(file_receipts, "same_filesystem", separate_filesystems)
    target = home / "new directory" / "managed"
    if existed:
        target.parent.mkdir()
        target.write_bytes(b"original")
    operation = apply_files(
        {target: b"managed"},
        expected={target: file_receipts.digest(b"original") if existed else None},
    )
    with target.open("r+b") as editor:
        restore(operation.name, apply=True)
        editor.seek(0)
        editor.write(b"saved after restore")
    if existed:
        assert target.read_bytes() == b"original"
    else:
        assert not target.parent.exists()
    row = file_receipts.load_operation(operation).files[0]
    assert row.retained_after is not None
    retained = home / row.retained_after.relative / "after"
    if separate:
        assert not retained.is_relative_to(home / "state")
    assert retained.read_bytes() == b"saved after restore"
    assert str(retained) in restore(operation.name)[0][1]
