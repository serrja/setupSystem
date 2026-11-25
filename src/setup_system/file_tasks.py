"""Narrow journal-backed filesystem tasks executed by the local Make recipes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from enum import StrEnum
from pathlib import Path

import typer
from pydantic import Field, StrictBool, TypeAdapter

from setup_system.application.recovery import recover
from setup_system.domain.actions import FileAction, RestoreAction, SystemAction
from setup_system.domain.contracts import MAX_MESSAGE_BYTES, ContractModel
from setup_system.domain.observations import ManagedPreview, NativeObservations, ProjectObservations
from setup_system.domain.receipts import FileState
from setup_system.infrastructure import codex_profile, receipt_repository, source_attempt
from setup_system.infrastructure.displays import preview_layout
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files, changed_files
from setup_system.infrastructure.operation_io import read_wire
from setup_system.infrastructure.receipt_migrations import restore_manifest
from setup_system.infrastructure.restore_files import LocalRecoveryFiles
from setup_system.layout import resource_root
from setup_system.machine_io import write_model
from setup_system.models import (
    BaseOptions,
    ProjectDirectory,
    RequestEnvelope,
    RestoreOptions,
    Status,
    TaskResult,
    WorkspaceOptions,
)


def restore(backup_id: str, *, apply: bool = False) -> list[tuple[str, str]]:
    folder = receipt_repository.operation_folder(backup_id)
    if receipt_repository.receipt_source(folder) is None:
        return restore_manifest(folder, apply=apply)
    return recover(folder, apply=apply, journal=receipt_repository, files=LocalRecoveryFiles())


def staged_files(stage: Path) -> tuple[dict[Path, bytes], dict[Path, int]]:
    """Only move ordinary files from the private staging tree into the caller's HOME."""
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError("Missing private staging tree")
    desired = {}
    modes = {}
    for path in sorted(stage.rglob("*")):
        info = path.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("Unsafe staged file ownership or mode")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Invalid staged file")
        dest = Path.home() / path.relative_to(stage)
        desired[dest] = path.read_bytes()
        modes[dest] = stat.S_IMODE(info.st_mode)
    return desired, modes


def file_preview(
    desired: dict[Path, bytes],
    modes: dict[Path, int],
    merges: dict[Path, FileState | None] | None = None,
) -> ManagedPreview:
    home = Path.home()
    changed = changed_files(desired, modes=modes, expected=merges)
    return ManagedPreview(
        changes=tuple(str(path.relative_to(home)) for path in changed),
        before={str(path.relative_to(home)): observe(path) for path in desired},
        after={
            str(path.relative_to(home)): (hashlib.sha256(data).hexdigest(), modes[path])
            for path, data in desired.items()
        },
    )


class FileTask(StrEnum):
    LAST_CHECK = "last-source-check"
    RECORD_CHECK = "record-source-check"
    BACKUPS = "backups"
    PREVIEW = "preview"
    PUBLISH = "publish"
    DISPLAY = "display-preview"
    PROJECT_CHECK = "project-check"
    RESTORE_PLAN = "restore-plan"
    RESTORE_APPLY = "restore-apply"


class SourceCheck(ContractModel):
    ok: StrictBool
    diagnostic: str = Field(max_length=MAX_MESSAGE_BYTES)


def publish_files(action: FileTask, envelope: RequestEnvelope, stage: Path) -> None:
    selection = TypeAdapter(BaseOptions | WorkspaceOptions).validate_python(envelope.settings)
    desired, modes = staged_files(stage)
    merges: dict[Path, FileState | None] = {}
    if isinstance(selection, WorkspaceOptions) and "codex" in selection.apps:
        profile, merges = codex_profile.files(resource_root() / "config")
        if desired.keys() & profile.keys():
            raise ValueError("Multiple profiles select the same Codex file")
        desired.update(profile)
        for path in profile:
            before = merges.get(path)
            modes[path] = before.mode if before else 0o600
    preview = file_preview(desired, modes, merges)
    if action == FileTask.PREVIEW:
        write_model(preview)
        return
    plan = envelope.expected
    if (
        plan is None
        or not plan.status.allows_confirmation
        or plan.blockers
        or not isinstance(plan.observed, NativeObservations)
        or not plan.observed.needs_user
        or tuple(item.path for item in plan.actions if isinstance(item, FileAction))
        != preview.changes
        or "files" not in plan.observed.model_fields_set
        or plan.observed.files != preview
    ):
        raise ValueError("Managed files changed since confirmation")
    expected = {Path.home() / name: state for name, state in preview.before.items()}
    typer.echo(apply_files(desired, modes=modes, expected=expected).name)


def recover_files(action: FileTask, envelope: RequestEnvelope) -> TaskResult:
    backup_id = RestoreOptions.model_validate(envelope.settings).backup_id
    rows = restore(backup_id)
    actions = tuple(
        RestoreAction.model_validate(
            dict(kind="restore", path=path, operation=status, label=f"{status} {path}")
        )
        for path, status in rows
        if status in {"would-restore", "would-create", "would-remove", "would-remove-directory"}
    )
    blockers = tuple(
        f"File conflict (changed after apply): {path}"
        for path, status in rows
        if status == "conflict"
    )
    if any(status == "manual-happ-restore" for _, status in rows):
        blockers += (
            "Restore private Happ data through Happ itself while the application is closed.",
        )
    reply = TaskResult(
        status=Status.BLOCKED if blockers else Status.READY if actions else Status.NO_CHANGES,
        actions=actions,
        observed=ProjectObservations(
            needs_system=False, needs_user=bool(actions), backup_id=backup_id
        ),
        blockers=blockers,
    )
    if action == FileTask.RESTORE_APPLY:
        if blockers or envelope.expected is None or not envelope.expected.same_plan(reply):
            raise ValueError("Restore preview changed; inspect it again")
        if actions:
            try:
                restore(backup_id, apply=True)
            except (OSError, RuntimeError, ValueError) as error:
                return TaskResult(status=Status.PARTIAL, blockers=(str(error),))
            return TaskResult(
                status=Status.COMPLETE, actions=actions, observed=reply.observed, blockers=()
            )
    return reply


cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def run(action: FileTask) -> None:
    """Run a single file operation for the packaged play or Make recipe."""
    if os.geteuid() == 0 or os.geteuid() != os.getuid():
        typer.echo("User files require a normal user", err=True)
        raise typer.Exit(2)
    if action == FileTask.LAST_CHECK:
        typer.echo(source_attempt.previous())
        return
    if action == FileTask.RECORD_CHECK:
        raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("Source check exceeds the supported size")
        check = SourceCheck.model_validate_json(raw)
        source_attempt.remember(source_attempt.observe(check.ok, check.diagnostic))
        return
    if action == FileTask.BACKUPS:
        typer.echo(json.dumps([name for name, _ in receipt_repository.list_operations()]))
        return
    request = Path(os.environ["SETUP_SYSTEM_REQUEST"])
    result = Path(os.environ["SETUP_SYSTEM_RESULT"])
    envelope = RequestEnvelope.model_validate_json(read_wire(request))
    if action in {FileTask.PREVIEW, FileTask.PUBLISH}:
        publish_files(action, envelope, result.parent / "files")
        return
    envelope.for_target(action)
    if action == FileTask.DISPLAY:
        preview_layout(WorkspaceOptions.model_validate(envelope.settings).monitors)
        reply = TaskResult(
            status=Status.COMPLETE,
            actions=(
                SystemAction(kind="display_restored", label="Previous display layout restored"),
            ),
        )
    elif action == FileTask.PROJECT_CHECK:
        directory = ProjectDirectory.model_validate(envelope.settings).project
        blockers = () if directory.is_dir() else ("Project directory is missing",)
        reply = TaskResult(
            status=Status.BLOCKED if blockers else Status.READY,
            actions=(),
            observed=ProjectObservations(
                needs_system=False, needs_user=True, directory=str(directory)
            ),
            blockers=blockers,
        )
    else:
        reply = recover_files(action, envelope)
    with result.open("w") as stream:
        write_model(reply, stream)


if __name__ == "__main__":
    cli()
