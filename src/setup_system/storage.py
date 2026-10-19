"""Atomic managed writes and private, hash-checked configuration backups."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path


def state_root() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "setup-system"


def _inside_home(path: Path) -> Path:
    home = Path.home().resolve()
    candidate = path.expanduser().absolute()
    try:
        candidate.relative_to(home)
    except ValueError as exc:
        raise ValueError(f"Managed path must be inside the current home: {candidate}") from exc
    for parent in (candidate, *candidate.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f"Managed path traverses a symlink: {parent}")
    return candidate


def atomic_write(
    path: Path, content: bytes, mode: int = 0o600, *, preserve_existing_mode: bool = True
) -> str:
    target = _inside_home(path)
    if target.exists():
        if not target.is_file():
            raise ValueError(f"Managed target is not a regular file: {target}")
        if target.read_bytes() == content:
            return "unchanged"
        if preserve_existing_mode:
            mode = stat.S_IMODE(target.stat().st_mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        candidate.chmod(mode)
        os.replace(candidate, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    return "changed"


def managed_block(original: str, body: str | None) -> str:
    start, end = "# >>> setupSystem >>>", "# <<< setupSystem <<<"
    if (
        original.count(start) > 1
        or original.count(end) > 1
        or original.count(start) != original.count(end)
    ):
        raise ValueError("Malformed setupSystem-owned block")
    if start in original:
        before, rest = original.split(start, 1)
        _, after = rest.split(end, 1)
        original = before.rstrip("\n") + after
    if body is None:
        return original.rstrip("\n") + "\n"
    return original.rstrip("\n") + f"\n\n{start}\n{body.rstrip()}\n{end}\n"


def new_backup(paths: list[Path]) -> Path:
    base = state_root() / "backups"
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = base / run_id
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    folder.chmod(0o700)
    home = Path.home().resolve()
    items: list[dict[str, str | int]] = []
    for source in sorted(set(paths)):
        target = _inside_home(source)
        if not target.exists():
            continue
        if not target.is_file():
            raise ValueError(f"Backup source is not a regular file: {target}")
        relative = target.relative_to(home)
        backup = folder / "files" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        backup.chmod(0o600)
        items.append(
            {
                "source": str(target),
                "home_relative": str(relative),
                "backup": str(backup.relative_to(folder)),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(target.stat().st_mode),
            }
        )
    manifest = folder / "manifest.json"
    manifest.write_text(
        json.dumps({"run_id": run_id, "items": items, "moves": []}, ensure_ascii=False, indent=2)
        + "\n"
    )
    manifest.chmod(0o600)
    return folder


def restore(
    backup_id: str, *, apply: bool = False, replace_current: bool = False
) -> list[tuple[str, str]]:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", backup_id):
        raise ValueError("Invalid backup ID")
    folder = state_root() / "backups" / backup_id
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("run_id") != backup_id:
        raise ValueError("Backup manifest ID mismatch")
    if manifest.get("application") == "happ" and apply:
        raise ValueError("Use setup-system happ restore for Happ state while the app is closed")
    prepared: list[tuple[Path, bytes, int, str]] = []
    for item in manifest["items"]:
        relative = Path(item.get("home_relative") or Path(item["source"]).relative_to(Path.home()))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe backup path")
        recorded_backup = Path(item["backup"])
        source = recorded_backup if recorded_backup.is_absolute() else folder / recorded_backup
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(folder.resolve())
        ):
            raise ValueError("Unsafe backup content")
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Backup hash mismatch")
        target = _inside_home(Path.home() / relative)
        if target.exists() and not target.is_file():
            raise ValueError(f"Restore target is not a regular file: {target}")
        if item.get("kind") == "sqliteSnapshot" and target.is_file():
            status = "database-present; review Happ restore"
        elif target.is_file():
            status = "unchanged" if target.read_bytes() == content else "conflict"
        else:
            status = "would-create"
        prepared.append((target, content, int(item["mode"]), status))
    if apply and replace_current:
        new_backup(
            [
                target
                for target, _, _, status in prepared
                if status == "conflict" and target.is_file()
            ]
        )
    actions: list[tuple[str, str]] = []
    for target, content, mode, status in prepared:
        if apply and (status == "would-create" or (status == "conflict" and replace_current)):
            atomic_write(target, content, mode, preserve_existing_mode=False)
            status = "restored"
        actions.append((str(target), status))
    return actions
