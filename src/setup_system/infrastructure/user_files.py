"""Apply reviewed user file changes with a private journal and local Ansible role."""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.infrastructure.file_receipts import (
    CreatedDirectory,
    FileChange,
    FileState,
    Operation,
    Ownership,
    checked_directory,
    digest,
    observe,
    ownership,
    target_path,
    writer,
)
from setup_system.infrastructure.theme import RESOURCE_ROOT
from setup_system.storage import atomic_write, state_root


def _changes(
    desired: Mapping[Path, str],
    modes: Mapping[Path, int],
    expected: Mapping[Path, str | None],
    index: Ownership,
) -> list[FileChange]:
    changes = []
    for path, checksum in desired.items():
        relative = path.relative_to(Path.home().resolve()).as_posix()
        target_path(relative)
        before = observe(path)
        current = before.sha256 if before else None
        if path in expected and current != expected[path]:
            raise ValueError(f"Configuration changed since inspection: {path}")
        mode = modes.get(path, before.mode if before else 0o644)
        if mode < 0 or mode & ~0o755 or mode & 0o022:
            raise ValueError(f"Unsafe managed file mode: {path}")
        parent = next(p for p in path.parents if p.is_dir())
        inherited_gid = parent.stat().st_gid if parent.stat().st_mode & 0o2000 else os.getegid()
        after = FileState(
            sha256=checksum,
            uid=os.getuid(),
            gid=before.gid if before else inherited_gid,
            mode=mode,
        )
        if before == after:
            continue
        owned = before is not None and index.files.get(str(path)) == current
        if before is not None and not owned and path not in expected:
            raise ValueError(f"Locally edited/unmanaged configuration requires review: {path}")
        changes.append(
            FileChange(
                relative=relative,
                before=before,
                after=after,
                backup="files/" + relative if before else None,
                previously_owned=owned,
            )
        )
    return changes


def changed_digests(
    desired: Mapping[Path, str],
    *,
    modes: Mapping[Path, int] | None = None,
    expected: Mapping[Path, str | None] | None = None,
) -> tuple[Path, ...]:
    """Compare release digests with actual file state without fetching or writing."""
    rows = _changes(desired, modes or {}, expected or {}, ownership())
    return tuple(Path.home().resolve() / row.relative for row in rows)


def changed_files(
    desired: Mapping[Path, bytes],
    *,
    modes: Mapping[Path, int] | None = None,
    expected: Mapping[Path, str | None] | None = None,
) -> tuple[Path, ...]:
    return changed_digests(
        {p: digest(data) for p, data in desired.items()}, modes=modes, expected=expected
    )


def _create_parents(path: Path, operation: Operation, folder: Path) -> None:
    missing = []
    for parent in path.parents:
        if parent.exists():
            break
        missing.append(parent)
    for parent in reversed(missing):
        checked_directory(parent)
        parent.mkdir(mode=0o700)
        info = parent.stat()
        operation.directories.append(
            CreatedDirectory(
                relative=parent.relative_to(Path.home().resolve()).as_posix(),
                device=info.st_dev,
                inode=info.st_ino,
            )
        )
        operation.save(folder)


def _run_role(
    changes: list[FileChange],
    desired: Mapping[Path, bytes],
    record_write: Callable[[Path], None],
) -> int:
    import ansible_runner  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory(prefix="setup-system-ansible-") as temporary:
        stage = Path(temporary)
        cfg = stage / "ansible.cfg"
        cfg.write_text("[defaults]\nhost_key_checking=True\nretry_files_enabled=False\n")
        entries = []
        for number, row in enumerate(changes):
            path = target_path(row.relative)
            if observe(path) != row.before:
                raise ValueError(f"File changed before applying configuration: {path}")
            source = stage / str(number)
            source.write_bytes(desired[path])
            entries.append(
                {
                    "source": str(source),
                    "destination": str(path),
                    "mode": format(row.after.mode, "04o"),
                    "group": str(row.after.gid),
                    "before": row.before.sha256 if row.before else "",
                    "before_mode": format(row.before.mode, "04o") if row.before else "",
                    "before_uid": row.before.uid if row.before else -1,
                    "before_gid": row.before.gid if row.before else -1,
                }
            )
        playbook: list[dict[str, Any]] = [
            {
                "name": "Apply selected user files",
                "hosts": "localhost",
                "gather_facts": False,
                "connection": "local",
                "become": False,
                "roles": [{"role": str(RESOURCE_ROOT / "roles/user_files")}],
            }
        ]

        def on_event(event: dict[str, Any]) -> bool:
            if event.get("event") == "runner_item_on_ok":
                result_data = event.get("event_data", {}).get("res", {})
                destination = result_data.get("dest")
                if isinstance(destination, str):
                    record_write(Path(destination))
            return False

        result = ansible_runner.run(
            event_handler=on_event,
            private_data_dir=str(stage),
            playbook=playbook,
            inventory="localhost,",
            extravars={
                "user_files_managed_files": entries,
                "ansible_python_interpreter": sys.executable,
            },
            envvars={
                "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
                "ANSIBLE_CONFIG": str(cfg),
                "ANSIBLE_HOME": str(stage / "ansible"),
                "ANSIBLE_LOCAL_TEMP": str(stage / "local"),
                "ANSIBLE_REMOTE_TEMP": str(stage / "remote"),
            },
            quiet=True,
            timeout=300,
        )
        return int(result.rc)


def apply_files(
    desired: Mapping[Path, bytes],
    *,
    directories: tuple[Path, ...] = (),
    modes: Mapping[Path, int] | None = None,
    expected: Mapping[Path, str | None] | None = None,
) -> Path:
    """Write managed files, or merge confirmed inputs guarded by their exact before hashes."""
    if os.geteuid() == 0:
        raise ValueError("User configuration and Ansible cannot run as root")
    modes = modes or {}
    expected = expected or {}
    if not (modes.keys() | expected.keys()) <= desired.keys():
        raise ValueError("Expected state must belong to a selected target")
    for path in directories:
        checked_directory(path)
        target_path((path / ".setup-system-dir").relative_to(Path.home().resolve()).as_posix())
    digests = {path: digest(data) for path, data in desired.items()}
    index = ownership()
    changes = _changes(digests, modes, expected, index)
    if not changes and all(p.exists() for p in directories):
        return (
            state_root() / "backups" / index.last_operation
            if index.last_operation
            else state_root()
        )
    with writer():
        index = ownership(migrate=True)
        changes = _changes(digests, modes, expected, index)
        if not changes and all(p.exists() for p in directories):
            return state_root()
        backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        folder = checked_directory(state_root() / "backups") / backup_id
        folder.mkdir(mode=0o700, parents=True)
        op = Operation(
            run_id=backup_id,
            files=changes,
            requested_directories=[
                p.relative_to(Path.home().resolve()).as_posix() for p in directories
            ],
        )
        for row in changes:
            if row.backup is not None and row.before is not None:
                path = target_path(row.relative)
                original = path.read_bytes()
                if digest(original) != row.before.sha256 or observe(path) != row.before:
                    raise ValueError(f"File changed during backup: {path}")
                atomic_write(folder / row.backup, original)
        op.save(folder)
        by_path = {target_path(row.relative): row for row in changes}

        def record_write(path: Path) -> None:
            row = by_path.get(path)
            if row and observe(path) == row.after:
                row.verified = True
                op.save(folder)
                index.files[str(path)] = row.after.sha256
                index.last_operation = backup_id
                index.save()

        rc = -1
        failure: Exception | None = None
        try:
            for row in changes:
                _create_parents(target_path(row.relative), op, folder)
            for directory in directories:
                _create_parents(directory / ".setup-system-dir", op, folder)
            rc = _run_role(changes, desired, record_write) if changes else 0
        except Exception as exc:
            failure = exc
        finally:
            op.status = "partial"
            op.save(folder)
            for row in changes:
                try:
                    path = target_path(row.relative)
                    row.verified = (rc == 0 or row.verified) and observe(path) == row.after
                except (ValueError, OSError):
                    row.verified = False
                    continue
                if row.verified:
                    index.files[str(path)] = row.after.sha256
            index.last_operation = backup_id
            index.save()
            if rc == 0 and all(row.verified for row in changes):
                op.status = "complete"
            elif not any(row.verified for row in changes):
                op.status = "failed"
            op.save(folder)
        if op.status == "partial":
            reason = f": {error_text(failure)}" if failure else ""
            raise PartialApplyError(
                f"User configuration partially applied{reason}; inspect backup {backup_id}"
            ) from failure
        if failure is not None:
            raise failure
        if op.status == "failed":
            raise RuntimeError(f"User configuration failed readback; inspect backup {backup_id}")
        return folder
