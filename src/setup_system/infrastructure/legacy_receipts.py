"""Read the bounded file-operation formats published in v0.4.0."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from setup_system.infrastructure.config_store import safe_file
from setup_system.infrastructure.file_receipts import (
    FileChange,
    FileState,
    Operation,
    checked_directory,
    digest,
    read_object,
    target_path,
)
from setup_system.storage import state_root


def _legacy_steps(folder: Path, data: dict[str, Any], source: Path) -> tuple[dict[str, Any], str]:
    if source.name == "operation.json":
        return data, "user_files"
    if source.name == "workstation.json":
        if set(data) != {"profile", "files", "settings"} or data["profile"] not in {
            "fonts",
            "hyprland",
        }:
            raise ValueError("Unknown legacy desktop receipt")
        if data["settings"]:
            raise ValueError(
                "This legacy backup also changed desktop settings; manual recovery required"
            )
        rows = data["files"]
        if any(
            set(row) != {"path", "created", "after"} or type(row["created"]) is not bool
            for row in rows
        ):
            raise ValueError("Invalid legacy desktop file records")
        after = {row["path"]: row["after"] for row in rows}
        created = [row["path"] for row in rows if row["created"]]
    elif source.name == "git-profile.json":
        if (
            set(data) != {"application", "files", "status"}
            or data["application"] != "git-profile"
            or data["status"] != "complete"
        ):
            raise ValueError("Incomplete legacy Git operation requires manual recovery")
        rows = data["files"]
        if not isinstance(rows, list) or any(
            set(row) != {"path", "before", "after"} for row in rows
        ):
            raise ValueError("Invalid legacy Git file records")
        after = {row["path"]: row["after"] for row in rows}
        created = [row["path"] for row in rows if row["before"] is None]
    elif source.parent.name == "shell-applies":
        if (
            set(data) != {"backup_id", "login_shell_before", "zsh", "targets"}
            or data["backup_id"] != folder.name
        ):
            raise ValueError("Invalid legacy shell receipt")
        targets = data["targets"]
        if not isinstance(targets, dict) or any(
            set(row) != {"existed", "applied_sha256"} or type(row["existed"]) is not bool
            for row in targets.values()
        ):
            raise ValueError("Invalid legacy shell file records")
        after = {name: row["applied_sha256"] for name, row in targets.items()}
        created = [name for name, row in targets.items() if not row["existed"]]
    else:
        if (
            set(data)
            != {"backup_id", "path", "installed_sha256", "version", "archive_sha256", "source"}
            or data["backup_id"] != folder.name
        ):
            raise ValueError("Invalid legacy RTK receipt")
        after = {data["path"]: data["installed_sha256"]}
        return {"status": "complete", "after": after, "completed": after}, "rtk"
    if source.name in {"workstation.json", "git-profile.json"} and len(rows) != len(after):
        raise ValueError("Duplicate legacy file records")
    return {"status": "running", "after": after, "created": created}, source.name


def migrate_user_files(folder: Path, payload: dict[str, Any], source: Path) -> Operation:
    """Reconstruct v0.4.0 before and after evidence without claiming unknown writes."""
    data, kind = _legacy_steps(folder, payload, source)
    status = data.get("status")
    keys = set(data)
    if not (
        (keys == {"status", "created", "after"} and status == "running")
        or (keys == {"status", "completed", "after"} and status in {"complete", "partial"})
    ):
        raise ValueError("Unknown file receipt schema; review the original backup")
    after = data["after"]
    if not isinstance(after, dict) or not after:
        raise ValueError("Invalid v0.4.0 file list")
    completed = data.get("completed", {})
    if not isinstance(completed, dict) or any(after.get(k) != v for k, v in completed.items()):
        raise ValueError("Invalid v0.4.0 verification evidence")
    manifest_path = folder / "manifest.json"
    safe_file(manifest_path, os.getuid())
    manifest: dict[str, Any] = read_object(manifest_path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("run_id") != folder.name
        or manifest.get("moves") != []
    ):
        raise ValueError("Invalid v0.4.0 backup manifest")
    originals = {}
    owned: dict[str, str] = {}
    index = state_root() / "workstation.json"
    home = Path.home().resolve()
    for entry in manifest["items"]:
        absolute = entry["source"]
        relative = Path(absolute).relative_to(home).as_posix()
        if (
            absolute in originals
            or relative != entry["home_relative"]
            or entry["backup"] != "files/" + relative
            or (absolute not in after and absolute != str(index))
        ):
            raise ValueError("Inconsistent v0.4.0 backup entries")
        original = folder / entry["backup"]
        checked_directory(original.parent)
        safe_file(original, os.getuid())
        content = original.read_bytes()
        if digest(content) != entry["sha256"]:
            raise ValueError("Invalid v0.4.0 backup checksum")
        originals[absolute] = entry
        if absolute == str(index):
            owned = json.loads(content)
            if not isinstance(owned, dict):
                raise ValueError("Invalid previous ownership index")
    if "created" in data and (
        not isinstance(data["created"], list) or set(data["created"]) != set(after) - set(originals)
    ):
        raise ValueError("Inconsistent v0.4.0 creation evidence")
    if kind == "git-profile.json" and any(
        row["before"] != originals.get(row["path"], {}).get("sha256") for row in payload["files"]
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
            before = FileState(sha256=info["sha256"], mode=info["mode"], **owner)
            backup = info["backup"]
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
    return Operation(run_id=folder.name, status=status, files=rows)
