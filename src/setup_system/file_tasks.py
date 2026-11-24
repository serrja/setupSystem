"""Narrow journal-backed filesystem tasks executed by the local Make recipes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from pydantic import TypeAdapter

from setup_system.application.recovery import recover
from setup_system.domain.actions import FileAction, SystemAction
from setup_system.domain.observations import ManagedPreview
from setup_system.domain.receipts import FileState
from setup_system.infrastructure import codex_profile, receipt_repository, source_attempt
from setup_system.infrastructure.displays import preview_layout
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files, changed_files
from setup_system.infrastructure.operation_io import read_wire
from setup_system.infrastructure.receipt_migrations import restore_manifest
from setup_system.infrastructure.restore_files import LocalRecoveryFiles
from setup_system.models import BaseOptions, RequestEnvelope, TaskResult, WorkspaceOptions
from setup_system.runner import resource_root


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
) -> dict[str, object]:
    home = Path.home()
    changed = changed_files(desired, modes=modes, expected=merges)
    before = {}
    after = {}
    for path, data in desired.items():
        name = str(path.relative_to(home))
        observed = observe(path)
        before[name] = observed.model_dump() if observed else None
        after[name] = [hashlib.sha256(data).hexdigest(), modes[path]]
    return {
        "changes": [str(p.relative_to(home)) for p in changed],
        "before": before,
        "after": after,
    }


def main() -> None:
    if os.getuid() == 0:
        raise SystemExit("User files require a normal user")
    if sys.argv[1:] == ["last-source-check"]:
        print(source_attempt.previous())
        return
    if sys.argv[1:] == ["record-source-check"]:
        check = json.load(sys.stdin)
        source_attempt.remember(
            source_attempt.observe(check["ok"] is True, str(check["diagnostic"]))
        )
        return
    if sys.argv[1:] == ["backups"]:
        print(json.dumps([name for name, _ in receipt_repository.list_operations()]))
        return
    request = Path(os.environ["SETUP_SYSTEM_REQUEST"])
    result = Path(os.environ["SETUP_SYSTEM_RESULT"])
    (action,) = sys.argv[1:]
    envelope = RequestEnvelope.model_validate_json(read_wire(request))
    if action not in {"preview", "publish"}:
        envelope.for_target(action)
    payload = envelope.model_dump(mode="json", by_alias=True, exclude_unset=True)

    if action in {"preview", "publish"}:
        TypeAdapter(BaseOptions | WorkspaceOptions).validate_python(envelope.settings)
        desired, modes = staged_files(result.parent / "files")
        merges: dict[Path, FileState | None] = {}
        if "codex" in payload["settings"].get("apps", []):
            profile, merges = codex_profile.files(resource_root() / "config")
            if desired.keys() & profile.keys():
                raise ValueError("Multiple profiles select the same Codex file")
            desired.update(profile)
            for path in profile:
                before = merges.get(path)
                modes[path] = before.mode if before else 0o600
        preview = file_preview(desired, modes, merges)
        if action == "preview":
            print(json.dumps(preview))
            return
        if (
            envelope.expected is None
            or envelope.expected.status not in {"ready", "plan_ready"}
            or envelope.expected.blockers
            or not envelope.expected.observed.needs_user
            or tuple(
                action.path
                for action in envelope.expected.actions
                if isinstance(action, FileAction)
            )
            != ManagedPreview.model_validate(preview).changes
            or envelope.expected.observed.get("files") is None
            or ManagedPreview.model_validate(envelope.expected.observed.get("files"))
            != ManagedPreview.model_validate(preview)
        ):
            raise ValueError("Managed files changed since confirmation")
        expected = {
            Path.home() / key: FileState.model_validate(value) if value is not None else None
            for key, value in payload["expected"]["observed"]["files"]["before"].items()
        }
        print(apply_files(desired, modes=modes, expected=expected).name)
        return
    settings = payload["settings"]
    observed: dict[str, object] = {"needs_system": False, "needs_user": True}
    blockers: list[str] = []
    if action == "display-preview":
        preview_layout(WorkspaceOptions.model_validate(settings).monitors)
        reply = TaskResult(
            status="complete",
            actions=(
                SystemAction(kind="display_restored", label="Previous display layout restored"),
            ),
        )
        result.write_text(reply.model_dump_json(exclude_unset=True))
        return
    if action == "project-check":
        directory = Path(settings["project"]).expanduser()
        if not directory.is_absolute():
            raise ValueError("Choose an absolute project path")
        if not directory.is_dir():
            blockers.append("Project directory is missing")
        observed["directory"] = str(directory)
        actions = []
    elif action in {"restore-plan", "restore-apply"}:
        rows = restore(settings["backup_id"])
        actions = [
            {"kind": "restore", "path": path, "operation": status, "label": f"{status} {path}"}
            for path, status in rows
            if status in {"would-restore", "would-remove", "would-remove-directory"}
        ]
        observed["needs_user"] = bool(actions)
        observed["backup_id"] = settings["backup_id"]
        blockers = [
            f"File conflict (changed after apply): {path}"
            for path, status in rows
            if status == "conflict"
        ]
    else:
        raise ValueError("Unknown file operation")
    answer = {
        "status": "blocked" if blockers else "ready",
        "actions": actions,
        "observed": observed,
        "blockers": blockers,
    }
    if action.startswith("restore-") and not actions and not blockers:
        answer["status"] = "no_changes"
    if action == "restore-apply":
        if (
            blockers
            or envelope.expected is None
            or not envelope.expected.same_plan(TaskResult.model_validate(answer))
        ):
            raise ValueError("Restore preview changed; inspect it again")
        if actions:
            restore(settings["backup_id"], apply=True)
            answer["status"] = "complete"
    result.write_text(TaskResult.model_validate(answer).model_dump_json(exclude_unset=True))


if __name__ == "__main__":
    main()
