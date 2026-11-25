"""Read the bounded file-operation formats published in v0.4.0."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictBool, TypeAdapter

from setup_system.domain.backup_manifest import BackupItem, BackupManifest
from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest
from setup_system.domain.receipts import FileChange, FileState, Operation, RunId, digest
from setup_system.infrastructure.user_storage import (
    checked_directory,
    inside_home,
    private_directory,
    read_object,
    safe_file,
    state_root,
    target_path,
)


class _Pending(ContractModel):
    status: Literal["running"] = "running"
    after: dict[str, Digest] = Field(min_length=1)
    created: tuple[str, ...]


class _Published(ContractModel):
    status: Literal["complete", "partial"]
    after: dict[str, Digest] = Field(min_length=1)
    completed: dict[str, Digest]


class _DesktopFile(ContractModel):
    path: str
    created: StrictBool
    after: Digest


class _Desktop(ContractModel):
    profile: Literal["fonts", "hyprland"]
    files: tuple[_DesktopFile, ...]
    settings: tuple[JsonValue, ...]


class _GitFile(ContractModel):
    path: str
    before: Digest | None
    after: Digest


class _Git(ContractModel):
    application: Literal["git-profile"]
    status: Literal["complete"]
    files: tuple[_GitFile, ...]


class _ShellFile(ContractModel):
    existed: StrictBool
    applied_sha256: Digest


class _Shell(ContractModel):
    backup_id: RunId
    login_shell_before: str
    zsh: str
    targets: dict[str, _ShellFile]


class _Rtk(ContractModel):
    backup_id: RunId
    path: str
    installed_sha256: Digest
    version: str
    archive_sha256: Digest
    source: str


_JOURNAL = TypeAdapter(Annotated[_Pending | _Published, Field(discriminator="status")])


def _legacy_steps(
    folder: Path, payload: dict[str, JsonValue], source: Path
) -> tuple[_Pending | _Published, str, dict[str, str | None]]:
    git_before: dict[str, str | None] = {}
    if source.name == "operation.json":
        return _JOURNAL.validate_python(payload), "user_files", git_before
    if source.name == "workstation.json":
        desktop = _Desktop.model_validate(payload)
        if desktop.settings:
            raise ValueError(
                "This legacy backup also changed desktop settings; manual recovery required"
            )
        after = {row.path: row.after for row in desktop.files}
        created = tuple(row.path for row in desktop.files if row.created)
        if len(desktop.files) != len(after):
            raise ValueError("Duplicate legacy desktop file records")
    elif source.name == "git-profile.json":
        git = _Git.model_validate(payload)
        after = {row.path: row.after for row in git.files}
        git_before = {row.path: row.before for row in git.files}
        created = tuple(path for path, before in git_before.items() if before is None)
        if len(git.files) != len(after):
            raise ValueError("Duplicate legacy Git file records")
    elif source.parent.name == "shell-applies":
        shell = _Shell.model_validate(payload)
        if shell.backup_id != folder.name:
            raise ValueError("Invalid legacy shell receipt")
        after = {name: item.applied_sha256 for name, item in shell.targets.items()}
        created = tuple(name for name, item in shell.targets.items() if not item.existed)
    elif source.parent.name == "rtk-installs":
        rtk = _Rtk.model_validate(payload)
        if rtk.backup_id != folder.name:
            raise ValueError("Invalid legacy RTK receipt")
        after = {rtk.path: rtk.installed_sha256}
        return _Published(status="complete", after=after, completed=after), "rtk", git_before
    else:
        raise ValueError("Unknown legacy file receipt")
    return _Pending(after=after, created=created), source.name, git_before


def migrate_user_files(folder: Path, payload: dict[str, JsonValue], source: Path) -> Operation:
    """Reconstruct v0.4.0 before and after evidence without claiming unknown writes."""
    journal, kind, git_before = _legacy_steps(folder, payload, source)
    after = journal.after
    completed = journal.completed if isinstance(journal, _Published) else {}
    if any(after.get(k) != v for k, v in completed.items()):
        raise ValueError("Invalid v0.4.0 verification evidence")
    manifest_path = folder / "manifest.json"
    manifest = BackupManifest.model_validate(read_object(manifest_path))
    if manifest.run_id != folder.name or manifest.application is not None:
        raise ValueError("Invalid v0.4.0 backup manifest")
    originals: dict[str, BackupItem] = {}
    owned: dict[str, str] = {}
    index = state_root() / "workstation.json"
    home = Path.home().resolve()
    for entry in manifest.items:
        absolute = entry.source
        relative = Path(absolute).relative_to(home).as_posix()
        if (
            absolute in originals
            or relative != entry.home_relative
            or entry.backup != "files/" + relative
            or (absolute not in after and absolute != str(index))
        ):
            raise ValueError("Inconsistent v0.4.0 backup entries")
        original = folder / entry.backup
        checked_directory(original.parent)
        safe_file(original, os.getuid())
        content = original.read_bytes()
        if digest(content) != entry.sha256:
            raise ValueError("Invalid v0.4.0 backup checksum")
        originals[absolute] = entry
        if absolute == str(index):
            owned = TypeAdapter(dict[str, str]).validate_json(content, strict=True)
    if isinstance(journal, _Pending) and set(journal.created) != set(after) - set(originals):
        raise ValueError("Inconsistent v0.4.0 creation evidence")
    if any(
        previous != (item.sha256 if (item := originals.get(path)) else None)
        for path, previous in git_before.items()
    ):
        raise ValueError("Git receipt does not match its original backup")
    if kind == "workstation.json":
        # The legacy executor wrote this index last. A prepared journal and
        # coincidentally matching bytes alone cannot prove ownership of a new file.
        checksum = after.get(str(index))
        candidates = (index, index.with_name("workstation.v0.json"))
        for candidate in candidates:
            safe_file(candidate, os.getuid())
            if candidate.is_file() and digest(candidate.read_bytes()) == checksum:
                break
        else:
            raise ValueError("Legacy desktop ownership could not be confirmed")
    rows = []
    for name, checksum in after.items():
        if name == str(index) and kind == "workstation.json":
            continue
        relative = Path(name).relative_to(home).as_posix()
        path = target_path(relative)
        before = None
        backup = None
        info = originals.get(name)
        # v0.4.0 did not record group IDs. Limit migration to the user's primary
        # group and default user directories; preserve ambiguous metadata for review.
        if (path.exists() and path.stat().st_gid != os.getegid()) or any(
            directory.exists() and directory.stat().st_mode & 0o2000
            for directory in path.parents
            if directory.is_relative_to(home)
        ):
            raise ValueError(f"Review legacy file group before recovery: {path}")
        owner = {"uid": os.getuid(), "gid": os.getegid()}
        if info:
            before = FileState(sha256=info.sha256, mode=info.mode, **owner)
            backup = info.backup
        mode = before.mode if before and kind != "user_files" else 0o644
        if kind == "rtk":
            mode = 0o755
        elif kind == "git-profile.json" and before is None:
            mode = 0o600
        elif path.name == "start-hyprland":
            mode = 0o700
        rows.append(
            FileChange(
                relative=relative,
                before=before,
                previously_owned=before is not None and owned.get(name) == before.sha256,
                after=FileState(sha256=checksum, mode=mode, **owner),
                backup=backup,
                verified=name in completed
                or (
                    kind != "user_files"
                    and path.is_file()
                    and digest(path.read_bytes()) == checksum
                ),
            )
        )
    return Operation(run_id=folder.name, status=journal.status, files=rows)


def restore_manifest(folder: Path, *, apply: bool) -> list[tuple[str, str]]:
    manifest_file = inside_home(folder / "manifest.json")
    manifest = BackupManifest.model_validate(read_object(manifest_file))
    if manifest.run_id != folder.name:
        raise ValueError("Backup manifest ID mismatch")
    if manifest.application == "happ" and apply:
        raise ValueError(
            "Restore private Happ state through Happ itself while the application is closed"
        )
    prepared: list[tuple[Path, bytes, int, str]] = []
    for item in manifest.items:
        relative = Path(item.home_relative or Path(item.source).relative_to(Path.home()))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("Unsafe backup path")
        recorded_backup = Path(item.backup)
        source = recorded_backup if recorded_backup.is_absolute() else folder / recorded_backup
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(folder.resolve())
        ):
            raise ValueError("Unsafe backup content")
        checked_directory(source.parent)
        safe_file(source, os.getuid())
        content = source.read_bytes()
        if digest(content) != item.sha256:
            raise ValueError("Backup hash mismatch")
        target = target_path(relative.as_posix())
        if Path(item.source) != target:
            raise ValueError("Backup target differs from its recorded path")
        if manifest.application == "happ":
            status = "manual-happ-restore"
        elif target.is_file():
            status = "unchanged" if target.read_bytes() == content else "conflict"
        else:
            status = "would-create"
        mode = item.mode
        if mode & ~0o755:
            raise ValueError("Unsafe mode in backup")
        prepared.append((target, content, mode, status))
    if apply and any(status == "conflict" for _, _, _, status in prepared):
        raise ValueError("A later edit conflicts with this backup; review the preview")
    actions: list[tuple[str, str]] = []
    for target, content, mode, status in prepared:
        if apply and status == "would-create":
            with private_directory(target.parent) as parent:
                with tempfile.NamedTemporaryFile(dir=f"/proc/self/fd/{parent}") as staged:
                    staged.write(content)
                    staged.flush()
                    os.fchmod(staged.fileno(), mode)
                    os.fsync(staged.fileno())
                    try:
                        os.link(staged.name, target.name, dst_dir_fd=parent, follow_symlinks=False)
                    except FileExistsError:
                        raise ValueError(f"File appeared during restore: {target}") from None
                os.fsync(parent)
            status = "restored"
        actions.append((str(target), status))
    return actions
