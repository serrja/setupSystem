"""An administrator can edit managed login files while a system operation is running."""

from pathlib import Path

import pytest
from test_desktop_system import system_fixture

from setup_system.infrastructure import login


def test_late_admin_edit_survives_login_rollback(tmp_path: Path, monkeypatch) -> None:
    system, account, environment, _, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    monkeypatch.setattr(login, "_require_root", lambda: None)
    command = system._command

    def run(args, **kwargs):
        if "set-default" in args and args[-1] == "graphical.target":
            system.paths.config.write_bytes(b"later administrator change\n")
            raise OSError("injected service failure")
        return command(args, **kwargs)

    monkeypatch.setattr(system, "_command", run)
    with pytest.raises(RuntimeError, match="recovery incomplete"):
        system.apply(plan)
    assert system.paths.config.read_bytes() == b"later administrator change\n"
    assert b'"partial"' in (system.paths.backups / "operation.json").read_bytes()


def test_identical_concurrent_creation_keeps_its_inode(tmp_path: Path, monkeypatch) -> None:
    system, account, environment, _, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    settings = next(item for item in plan.files if item.path == system.paths.config)
    move = system.files.move_new
    created = []

    def publish(source, destination):
        if destination == settings.path:
            destination.write_bytes(settings.content)
            destination.chmod(settings.mode)
            created.append(destination.stat().st_ino)
        return move(source, destination)

    monkeypatch.setattr(system.files, "move_new", publish)
    monkeypatch.setattr(login, "_require_root", lambda: None)
    with pytest.raises((OSError, RuntimeError)):
        system.apply(plan)
    assert created and settings.path.stat().st_ino == created[0]


def test_completed_receipt_is_retracted_on_final_checkpoint_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from setup_system.infrastructure.login_recovery import LoginRecovery

    system, account, environment, _, snapshot = system_fixture(tmp_path)
    plan = system.plan(account, environment, snapshot=snapshot)
    checkpoint = LoginRecovery.checkpoint

    def fail(operation, status):
        if status == "complete":
            assert system.paths.receipt.exists()
            raise OSError("Failed checkpoint")
        checkpoint(operation, status)

    monkeypatch.setattr(login, "_require_root", lambda: None)
    monkeypatch.setattr(LoginRecovery, "checkpoint", fail)
    with pytest.raises(OSError, match="Failed checkpoint"):
        system.apply(plan)
    assert not system.paths.receipt.exists()
    assert all(not item.path.exists() for item in plan.files)
