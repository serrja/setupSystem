"""Exercise managed file publication and recovery in a synthetic HOME/XDG tree."""

import json
from pathlib import Path

import pytest
from historical_receipts import historical_backup

from setup_system.infrastructure.managed_files import apply_files


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
    from setup_system.infrastructure import managed_files

    first = home / "config/first"
    second = home / "config/second"

    def partial(rows, callback):
        callback(managed_files.publish_prepared(rows[0]))
        raise OSError("Interrupted between two files")

    original = managed_files._publish_changes
    monkeypatch.setattr(managed_files, "_publish_changes", partial)
    with pytest.raises(RuntimeError, match="partially"):
        apply_files({first: b"first", second: b"second"})
    receipt = json.loads((home / "state/setup-system/workstation.json").read_text())
    assert str(first) in receipt["files"] and str(second) not in receipt["files"]
    monkeypatch.setattr(managed_files, "_publish_changes", original)
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
    from setup_system.application.recovery import restore

    existing = home / "config/tool/first"
    created = home / "config/nested/new"
    apply_files({existing: b"first"})
    changed = apply_files({existing: b"second", created: b"new"})
    restore(changed.name, apply=True)
    assert existing.read_bytes() == b"first"
    assert not created.exists()
    assert not created.parent.exists()
    restore(changed.name, apply=True)
    assert existing.read_bytes() == b"first"


@pytest.mark.parametrize("later", ["changed", "mode", "recreated", "symlink"])
def test_repeated_restore_reports_later_changes_without_overwriting(home: Path, later: str) -> None:
    from setup_system.application.recovery import restore

    target = home / "managed"
    if later != "recreated":
        apply_files({target: b"original"}, modes={target: 0o600})
    operation = apply_files({target: b"managed"})
    restore(operation.name, apply=True)
    receipt = operation / "operation.json"
    ownership = home / "state/setup-system/workstation.json"
    records = [(file.read_bytes(), file.stat().st_mtime_ns) for file in (receipt, ownership)]
    outside = home / "owner"
    outside.write_bytes(b"original")
    if later == "symlink":
        target.unlink()
        target.symlink_to(outside)
    elif later == "mode":
        target.chmod(0o640)
    else:
        target.write_bytes(b"managed")

    assert restore(operation.name) == [(str(target), "conflict")]
    with pytest.raises(ValueError, match="later edit"):
        restore(operation.name, apply=True)
    if later == "symlink":
        assert target.readlink() == outside and outside.read_bytes() == b"original"
    elif later == "mode":
        assert target.stat().st_mode & 0o777 == 0o640 and target.read_bytes() == b"original"
    else:
        assert target.read_bytes() == b"managed"
    assert [
        (file.read_bytes(), file.stat().st_mtime_ns) for file in (receipt, ownership)
    ] == records


def test_interrupted_runner_keeps_recovery_and_only_verified_writes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.application.recovery import restore
    from setup_system.infrastructure import managed_files
    from setup_system.infrastructure.receipt_repository import list_operations

    selected = home / "config/tool/selected"
    pending = home / "missing/tool/pending"

    def interrupt(rows, callback):
        callback(managed_files.publish_prepared(rows[0]))
        raise KeyboardInterrupt

    monkeypatch.setattr(managed_files, "_publish_changes", interrupt)
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
    from setup_system.infrastructure import managed_files
    from setup_system.infrastructure.receipt_repository import list_operations

    monkeypatch.setattr(managed_files, "_publish_changes", lambda *_: None)
    with pytest.raises(RuntimeError, match="failed readback"):
        apply_files({home / "file": b"expected"})
    assert list_operations()[0][1] == "failed"


def test_legacy_journal_migration_backs_up_originals_and_preserves_modes(home: Path) -> None:
    from setup_system.application.recovery import restore
    from setup_system.domain.receipts import digest
    from setup_system.infrastructure.user_storage import state_root

    original = home / "tool/owned"
    original.parent.mkdir()
    original.write_bytes(b"before")
    original.chmod(0o640)
    index = state_root() / "workstation.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({str(original): digest(b"before")}))
    legacy = historical_backup([original, index])
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
    from setup_system.application.recovery import restore

    target = home / "original"
    target.write_bytes(b"before")
    backup = historical_backup([target])
    target.write_bytes(b"after")
    journal = backup / "operation.json"
    journal.write_text('{"schema_version":21}')
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        restore(backup.name, apply=True)
    assert before == {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}


def test_restore_refuses_later_mode_change_without_touching_a_sibling(home: Path) -> None:
    from setup_system.application.recovery import restore

    one, two = home / "one", home / "two"
    op = apply_files({one: b"first", two: b"second"})
    two.chmod(0o600)
    with pytest.raises(ValueError, match="later edit"):
        restore(op.name, apply=True)
    assert one.read_bytes() == b"first" and two.stat().st_mode & 0o777 == 0o600


def test_concurrent_restore_edit_is_returned_without_overwriting(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from setup_system.application.recovery import restore

    target = home / "tool"
    op = apply_files({target: b"managed"})
    from setup_system.infrastructure import file_publication

    original = file_publication.move_new

    def concurrent_edit(source, destination):
        if source == target:
            target.write_bytes(b"owner edited during restore")
        return original(source, destination)

    monkeypatch.setattr(file_publication, "move_new", concurrent_edit)
    with pytest.raises(ValueError, match="later edit"):
        restore(op.name, apply=True)
    assert target.read_bytes() == b"owner edited during restore"


def test_interrupted_restore_resumes_from_private_staging(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.application.recovery import restore
    from setup_system.infrastructure import restore_files

    target = home / "tool"
    apply_files({target: b"first"})
    op = apply_files({target: b"second"})
    original = restore_files._publish_original

    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(restore_files, "_publish_original", interrupt)
    with pytest.raises(KeyboardInterrupt):
        restore(op.name, apply=True)
    monkeypatch.setattr(restore_files, "_publish_original", original)
    assert restore(op.name) == [(str(target), "would-restore")]
    restore(op.name, apply=True)
    assert target.read_bytes() == b"first"
    assert not list(home.glob(".setup-system-restore-*"))


def test_legacy_desktop_preparation_requires_committed_index(home: Path) -> None:
    from setup_system.application.recovery import restore
    from setup_system.domain.receipts import digest
    from setup_system.infrastructure.user_storage import state_root

    managed = home / "font.conf"
    index = state_root() / "workstation.json"
    backup = historical_backup([])
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
    from setup_system.application.recovery import restore
    from setup_system.domain.receipts import digest

    config = home / ".gitconfig"
    config.write_bytes(b"old")
    backup = historical_backup([config])
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
    from setup_system.domain.receipts import digest
    from setup_system.infrastructure import managed_files

    target = home / "ordinary-editor.txt"
    if existed:
        target.write_bytes(b"confirmed owner input")
    original = managed_files._publish_changes
    injected = False

    def during_publication(rows, callback):
        nonlocal injected
        target.write_bytes(b"owner edit during publication")
        injected = True
        return original(rows, callback)

    monkeypatch.setattr(managed_files, "_publish_changes", during_publication)
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
    import asyncio

    from setup_system.application.rollback import RecoveryPlan
    from setup_system.domain import receipts
    from setup_system.infrastructure import file_publication, managed_files, receipt_repository
    from setup_system.plugin_manager import PluginManager
    from setup_system_sdk import Outcome

    target = home / "tool"
    target.write_bytes(b"owner's original")
    before = receipts.digest(target.read_bytes())
    original = file_publication.move_new

    def interrupt(source, destination):
        original(source, destination)
        if source == target:
            raise KeyboardInterrupt

    def run_role(rows, callback):
        for row in rows:
            callback(file_publication.publish_prepared(row))
        return 0

    monkeypatch.setattr(file_publication, "move_new", interrupt)
    monkeypatch.setattr(managed_files, "_publish_changes", run_role)
    with pytest.raises(KeyboardInterrupt):
        apply_files({target: b"selected"}, expected={target: before})
    assert not target.exists()
    ((operation, status),) = receipt_repository.list_operations()
    assert status == "partial"
    unrelated = home / "unrelated"
    apply_files({unrelated: b"independent"})
    assert unrelated.read_bytes() == b"independent"
    with pytest.raises(ValueError, match="Restore interrupted file publication"):
        apply_files({target: b"selected"})
    assert not target.exists()
    plan = RecoveryPlan.inspect(operation)
    assert plan.entries == ((str(target), "would-return-displaced"),)
    assert not target.exists()
    applied = asyncio.run(PluginManager().run_recovery(plan))
    assert applied.outcome is Outcome.COMPLETE
    assert target.read_bytes() == b"owner's original"


def test_restore_after_publication_before_verification_is_journaled(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.application.recovery import restore
    from setup_system.infrastructure import managed_files, receipt_repository

    target = home / "first-publication"
    save_operation = managed_files.save_operation

    def interrupt(folder, operation):
        if target.exists():
            raise KeyboardInterrupt
        save_operation(folder, operation)

    monkeypatch.setattr(managed_files, "save_operation", interrupt)
    with pytest.raises(KeyboardInterrupt):
        apply_files({target: b"published"})
    ((run_id, _),) = receipt_repository.list_operations()
    folder = receipt_repository.operation_folder(run_id)
    assert not receipt_repository.load_operation(folder).files[0].verified
    assert target.read_bytes() == b"published"
    assert restore(run_id) == [(str(target), "would-remove")]
    assert not receipt_repository.load_operation(folder).files[0].verified
    assert restore(run_id, apply=True) == [(str(target), "removed")]
    assert not target.exists()


def test_owner_recreates_target_while_prior_inode_is_held(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from setup_system.application.recovery import restore
    from setup_system.diagnostics import PartialApplyError
    from setup_system.domain import receipts
    from setup_system.infrastructure import file_publication, managed_files, receipt_repository

    target = home / "config.ini"
    target.write_bytes(b"prior file")
    move = file_publication.move_new

    def racing_move(source, dest):
        move(source, dest)
        if source == target:
            target.write_bytes(b"owner's replacement")

    def run_role(rows, callback):
        for row in rows:
            callback(file_publication.publish_prepared(row))
        return 0

    monkeypatch.setattr(file_publication, "move_new", racing_move)
    monkeypatch.setattr(managed_files, "_publish_changes", run_role)
    with pytest.raises(PartialApplyError):
        apply_files({target: b"wanted"}, expected={target: receipts.digest(b"prior file")})
    assert target.read_bytes() == b"owner's replacement"
    ((operation, _),) = receipt_repository.list_operations()
    op = receipt_repository.load_operation(receipt_repository.operation_folder(operation))
    retained = file_publication.apply_stage(op.files[0], target) / "original"
    assert retained.read_bytes() == b"prior file"
    with pytest.raises(ValueError, match="later edit"):
        restore(operation, apply=True)
    assert target.read_bytes() == b"owner's replacement" and retained.is_file()


def test_open_writer_on_displaced_inode_keeps_its_late_edit(home: Path) -> None:
    from setup_system.application.recovery import restore
    from setup_system.domain import receipts
    from setup_system.infrastructure import file_publication, receipt_repository

    target = home / "existing"
    target.write_bytes(b"checked")
    with target.open("wb") as editor:
        editor.write(b"checked")
        editor.flush()
        operation = apply_files(
            {target: b"configured"}, expected={target: receipts.digest(b"checked")}
        )
        editor.seek(0)
        editor.write(b"editor saved later")
    row = receipt_repository.load_operation(operation).files[0]
    retained = file_publication.apply_stage(row, target) / "original"
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
    from setup_system.application.recovery import restore
    from setup_system.domain import receipts
    from setup_system.infrastructure import managed_files, receipt_repository, restore_files

    if separate:

        def separate_filesystems(left, right):
            return left.is_relative_to(home / "state") == right.is_relative_to(home / "state")

        monkeypatch.setattr(managed_files, "same_filesystem", separate_filesystems)
        monkeypatch.setattr(restore_files, "same_filesystem", separate_filesystems)
    target = home / "new directory" / "managed"
    if existed:
        target.parent.mkdir()
        target.write_bytes(b"original")
    operation = apply_files(
        {target: b"managed"},
        expected={target: receipts.digest(b"original") if existed else None},
    )
    with target.open("r+b") as editor:
        restore(operation.name, apply=True)
        editor.seek(0)
        editor.write(b"saved after restore")
    if existed:
        assert target.read_bytes() == b"original"
    else:
        assert not target.parent.exists()
    row = receipt_repository.load_operation(operation).files[0]
    assert row.retained_after is not None
    retained = home / row.retained_after.relative / "after"
    if separate:
        assert not retained.is_relative_to(home / "state")
    assert retained.read_bytes() == b"saved after restore"
    assert str(retained) in restore(operation.name)[0][1]


def test_unverified_v040_creation_cannot_claim_a_later_matching_file(home: Path) -> None:
    from setup_system.application.recovery import restore
    from setup_system.domain.receipts import digest

    target = home / "independently-created"
    backup = historical_backup([])
    raw = json.dumps(
        {"status": "partial", "after": {str(target): digest(b"same bytes")}, "completed": {}}
    ).encode()
    journal = backup / "operation.json"
    journal.write_bytes(raw)
    target.write_bytes(b"same bytes")
    target.chmod(0o644)
    assert restore(backup.name) == [(str(target), "conflict")]
    with pytest.raises(ValueError, match="later edit"):
        restore(backup.name, apply=True)
    assert target.read_bytes() == b"same bytes"
    assert journal.read_bytes() == raw
    assert not journal.with_name("operation.v0.json").exists()


def test_confirmed_file_plan_includes_permissions(home: Path) -> None:
    from setup_system_sdk import Inputs, PluginSettings
    from setup_system_sdk.native import FilesBackend

    target = home / "managed"

    class Example(FilesBackend):
        file_actions = frozenset({"configure"})

        def files(self, *args):
            return {target: b"second"}

    apply_files({target: b"first"}, modes={target: 0o600})
    backend = Example()
    original = backend.prepare("configure", PluginSettings(), Inputs())
    target.chmod(0o640)
    assert backend.prepare("configure", PluginSettings(), Inputs()) != original
    assert target.read_bytes() == b"first"


def test_project_directories_restore_through_make_and_preserve_other_files(home):
    import asyncio

    from setup_system.application.rollback import RecoveryPlan
    from setup_system.plugin_manager import PluginManager
    from setup_system_sdk import Outcome

    folder = home / "Projects"
    first, second = folder / "first", folder / "second"
    backup = apply_files({}, directories=(first, second))
    foreign = first / "owner.md"
    foreign.write_text("keep")
    preview = RecoveryPlan.inspect(backup.name)
    assert (str(second), "would-remove-directory") in preview.entries
    assert (str(first), "would-remove-directory") not in preview.entries
    assert asyncio.run(PluginManager().run_recovery(preview)).outcome is Outcome.COMPLETE
    assert foreign.read_text() == "keep" and not second.exists()


def test_migration_preserves_backup_created_during_publication(home, monkeypatch):
    import os
    import stat

    from setup_system.infrastructure.receipt_repository import _backup_original

    original = home / "operation.json"
    original.write_bytes(b'{"owner":"original"}')
    backup = home / "operation.v0.json"
    fsync = os.fsync

    def concurrent_create(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode) and not backup.exists():
            backup.write_bytes(b'{"owner":"concurrent"}')
        fsync(descriptor)

    monkeypatch.setattr(os, "fsync", concurrent_create)
    with pytest.raises(ValueError, match="Conflicting"):
        _backup_original(original, original.read_bytes())
    assert original.read_bytes() == b'{"owner":"original"}'
    assert backup.read_bytes() == b'{"owner":"concurrent"}'


@pytest.mark.parametrize("race", ["edit-operation", "new-destination", "edit-ownership"])
def test_receipt_migration_refuses_a_competing_writer(home, monkeypatch, race):
    from setup_system.domain.receipts import digest
    from setup_system.infrastructure import receipt_repository as journal

    folder = historical_backup([])
    destination = folder / "operation.json"
    values = {str(home / "managed"): digest(b"installed")}
    original = json.dumps({"status": "complete", "after": values, "completed": values}).encode()
    destination.write_bytes(original)
    concurrent = b'{"owner": "keep this edit"}'

    if race == "new-destination":
        destination.unlink()
        source = folder / "git-profile.json"
        source.write_text(
            json.dumps(
                {
                    "application": "git-profile",
                    "status": "complete",
                    "files": [
                        {
                            "path": str(home / "managed"),
                            "before": None,
                            "after": digest(b"installed"),
                        }
                    ],
                }
            )
        )
        migrate = journal.migrate_user_files

        def concurrent_create(*args):
            result = migrate(*args)
            destination.write_bytes(concurrent)
            return result

        monkeypatch.setattr(journal, "migrate_user_files", concurrent_create)
    else:
        if race == "edit-ownership":
            destination = journal.state_root() / "workstation.json"
            original = json.dumps(values).encode()
            destination.write_bytes(original)
        backup = destination.with_name(destination.stem + ".v0.json")
        fsync = journal.os.fsync
        changed = False

        def edit_after_backup(descriptor):
            nonlocal changed
            fsync(descriptor)
            if backup.exists() and not changed:
                changed = True
                destination.write_bytes(concurrent)

        monkeypatch.setattr(journal.os, "fsync", edit_after_backup)

    with pytest.raises((ValueError, FileExistsError)):
        if race == "edit-ownership":
            journal.save_ownership(journal.ownership(migrate=True))
        else:
            journal.load_operation(folder, migrate=True)
    assert destination.read_bytes() == concurrent
    if race != "new-destination":
        assert backup.read_bytes() == original


def test_receipt_migration_detects_changed_separate_source_after_publication(home, monkeypatch):
    from setup_system.domain.receipts import digest
    from setup_system.infrastructure import receipt_repository as journal
    from setup_system.infrastructure import user_storage

    folder = historical_backup([])
    source = folder / "git-profile.json"
    source.write_text(
        json.dumps(
            {
                "application": "git-profile",
                "status": "complete",
                "files": [
                    {"path": str(home / "tool"), "before": None, "after": digest(b"applied")}
                ],
            }
        )
    )
    destination = folder / "operation.json"
    fsync = user_storage.os.fsync
    raced = False

    def edit_at_publication(fd):
        nonlocal raced
        fsync(fd)
        if destination.exists() and not raced:
            raced = True
            source.write_text("new owner data")

    monkeypatch.setattr(user_storage.os, "fsync", edit_at_publication)
    with pytest.raises(ValueError, match="Concurrent"):
        journal.load_operation(folder, migrate=True)
    assert raced and source.read_text() == "new owner data"
    assert not destination.exists()


def test_migrated_receipt_keeps_open_editor_inode_recoverable(home):
    from setup_system.domain.receipts import digest
    from setup_system.infrastructure import receipt_repository as journal

    folder = historical_backup([])
    path = folder / "operation.json"
    values = {str(home / "tool"): digest(b"after")}
    raw = json.dumps({"status": "complete", "after": values, "completed": values}).encode()
    path.write_bytes(raw)
    with path.open("ab") as editor:
        journal.load_operation(folder, migrate=True)
        editor.write(b" late edit")
    assert (folder / "operation.v0.json").read_bytes() == raw
    assert next(folder.glob("operation.v0.retained-*")).read_bytes() == raw + b" late edit"
    assert journal.load_operation(folder).status == "complete"
