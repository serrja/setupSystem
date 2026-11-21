"""Inspect block and directory encryption without unlocking or changing storage."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from setup_system.domain.system_facts import EncryptionInfo, EncryptionState
from setup_system.infrastructure.inspection_commands import (
    InspectionRunner,
    read_query,
    run_inspection,
    safe_absolute,
)

FS_ENCRYPT_FL = 0x00000800


DirectoryFlags = Callable[[Path], int | None]


@dataclass(frozen=True)
class _Mount:
    source: str
    filesystem: str


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Invalid inspection object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("Invalid inspection key")
        result[key] = item
    return result


def _mount(path: Path, run: InspectionRunner) -> _Mount | None:
    output = read_query(
        run,
        ["/usr/bin/findmnt", "--json", "--target", str(path), "--output", "SOURCE,FSTYPE"],
    )
    if output is None:
        return None
    try:
        filesystems = _object(json.loads(output)).get("filesystems")
        if not isinstance(filesystems, list) or len(filesystems) != 1:
            return None
        value = _object(filesystems[0])
        source, filesystem = value.get("source"), value.get("fstype")
        if not isinstance(source, str) or not isinstance(filesystem, str):
            return None
        return _Mount(source, filesystem.lower())
    except (ValueError, TypeError, RecursionError):
        return None


def _block_layers(source: str, run: InspectionRunner) -> tuple[str, ...] | None:
    # Btrfs findmnt sources may append a subvolume; only the device is queried.
    device = source.partition("[")[0]
    if (
        not re.fullmatch(r"/dev/[A-Za-z0-9_+./:-]+", device)
        or ".." in Path(device).parts
        or len(device) > 4096
    ):
        return None
    output = read_query(
        run,
        [
            "/usr/bin/lsblk",
            "--json",
            "--inverse",
            "--tree",
            "--output",
            "NAME,TYPE,FSTYPE",
            "--",
            device,
        ],
    )
    if output is None:
        return None
    try:
        devices = _object(json.loads(output)).get("blockdevices")
        if not isinstance(devices, list) or not devices:
            return None
        pending = [(item, frozenset[str]()) for item in devices]
        layers: set[str] = set()
        count = 0
        while pending:
            item, ancestors = pending.pop()
            value = _object(item)
            count += 1
            name, kind, filesystem = value.get("name"), value.get("type"), value.get("fstype")
            if (
                count > 4096
                or len(ancestors) >= 64
                or not isinstance(name, str)
                or not name
                or name in ancestors
                or not isinstance(kind, str)
                or (filesystem is not None and not isinstance(filesystem, str))
            ):
                return None
            if kind == "crypt":
                layers.add("dm-crypt")
            if isinstance(filesystem, str) and filesystem.lower() == "crypto_luks":
                layers.add("LUKS")
            children = value.get("children", [])
            if not isinstance(children, list):
                return None
            pending.extend((child, ancestors | {name}) for child in children)
        return tuple(sorted(layers))
    except (ValueError, TypeError, RecursionError):
        return None


def root_encryption(path: Path, run: InspectionRunner) -> EncryptionInfo:
    return _mount_encryption(_mount(path, run), run)


def _mount_encryption(mount: _Mount | None, run: InspectionRunner) -> EncryptionInfo:
    if mount is None:
        return EncryptionInfo(
            state=EncryptionState.UNKNOWN, layers=(), detail="Mount information is unavailable"
        )
    if mount.filesystem == "ecryptfs":
        return EncryptionInfo(
            state=EncryptionState.ENCRYPTED, layers=("eCryptfs",), detail="Existing eCryptfs mount"
        )
    layers = _block_layers(mount.source, run)
    if layers:
        return EncryptionInfo(
            state=EncryptionState.ENCRYPTED,
            layers=layers,
            detail="Existing encrypted block ancestry",
        )
    return EncryptionInfo(
        state=EncryptionState.UNKNOWN,
        layers=(),
        detail="No conclusive encryption evidence; filesystem-level protection may still exist",
    )


def filesystem_flags(path: Path, *, run: InspectionRunner = run_inspection) -> int | None:
    """Use the filesystem's existing userspace attribute reader; never request a key."""
    output = read_query(run, ["/usr/bin/lsattr", "-d", "--", str(path)], missing_key=True)
    if output is None:
        return None
    flags = output.split(maxsplit=1)
    if not flags or re.fullmatch(r"[A-Za-z-]{1,64}", flags[0]) is None:
        return None
    return FS_ENCRYPT_FL if "E" in flags[0] else 0


def _fscrypt_unlocked(home: Path, run: InspectionRunner) -> bool | None:
    """Read only the selected directory's explicit key-presence result, never a key."""
    output = read_query(run, ["/usr/bin/fscrypt", "status", str(home)], missing_key=True)
    if output is None:
        return None
    states = re.findall(r"^Unlocked:[ \t]*(Yes|No)[ \t]*$", output, re.MULTILINE)
    return states[0] == "Yes" if len(states) == 1 else None


def home_encryption(
    home: Path, run: InspectionRunner, home_flags: DirectoryFlags
) -> EncryptionInfo:
    try:
        details = home.lstat()
        if not stat.S_ISDIR(details.st_mode):
            return EncryptionInfo(
                state=EncryptionState.UNKNOWN, layers=(), detail="HOME is not a regular directory"
            )
        try:
            (home / ".ecryptfs").lstat()
        except FileNotFoundError:
            ecryptfs_marker = False
        else:
            ecryptfs_marker = True
    except OSError as exc:
        if exc.errno == errno.ENOKEY:
            return EncryptionInfo(
                state=EncryptionState.LOCKED,
                layers=("filesystem encryption",),
                detail="HOME requires its existing key unlock",
            )
        return EncryptionInfo(
            state=EncryptionState.UNKNOWN,
            layers=(),
            detail="HOME protection could not be inspected",
        )
    mount = _mount(home, run)
    if ecryptfs_marker and (mount is None or mount.filesystem != "ecryptfs"):
        return EncryptionInfo(
            state=EncryptionState.LOCKED if mount is not None else EncryptionState.UNKNOWN,
            layers=("eCryptfs",),
            detail="Existing eCryptfs HOME is not verified as mounted; unlock it through "
            "the existing local login before setup",
        )
    result = _mount_encryption(mount, run)
    if mount is not None and mount.filesystem == "ecryptfs":
        return result
    try:
        flags = home_flags(home)
    except OSError as exc:
        if exc.errno == errno.ENOKEY:
            return EncryptionInfo(
                state=EncryptionState.LOCKED,
                layers=("filesystem encryption",),
                detail="HOME requires its existing key unlock",
            )
        return EncryptionInfo(
            state=EncryptionState.UNKNOWN,
            layers=(),
            detail="HOME protection could not be inspected",
        )
    if type(flags) is int and flags & FS_ENCRYPT_FL:
        layers = tuple(dict.fromkeys((*result.layers, "fscrypt")))
        try:
            unlocked = _fscrypt_unlocked(home, run)
        except OSError as exc:
            if exc.errno == errno.ENOKEY:
                unlocked = False
            else:
                unlocked = None
        if unlocked is True:
            return EncryptionInfo(
                state=EncryptionState.ENCRYPTED,
                layers=layers,
                detail="Existing fscrypt HOME is unlocked; preserve its PAM unlock integration",
            )
        if unlocked is False:
            return EncryptionInfo(
                state=EncryptionState.LOCKED,
                layers=layers,
                detail="Existing fscrypt HOME must be unlocked by normal login",
            )
        return EncryptionInfo(
            state=EncryptionState.UNKNOWN,
            layers=layers,
            detail="Existing fscrypt policy; HOME unlock state is unavailable "
            "and must be verified before setup",
        )
    return result


def validate_home_access(
    home: Path,
    *,
    run: InspectionRunner = run_inspection,
    home_flags: DirectoryFlags | None = None,
) -> EncryptionInfo:
    """Recheck encryption and directory access immediately before a user worker writes."""
    if not safe_absolute(str(home)):
        raise ValueError("HOME access requires an absolute path without traversal")
    try:
        descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ValueError(
            "HOME is locked or unreadable; use the existing local login first"
        ) from None
    try:
        opened = os.fstat(descriptor)
        flags = (lambda path: filesystem_flags(path, run=run)) if home_flags is None else home_flags
        protection = home_encryption(home, run, flags)
        if protection.state == EncryptionState.LOCKED or (
            protection.state == EncryptionState.UNKNOWN
            and any(layer.casefold() in {"fscrypt", "ecryptfs"} for layer in protection.layers)
        ):
            raise ValueError(
                "The encrypted HOME is locked or its unlock status is unknown; "
                "use the existing local login first"
            )
        # Consume at most one entry, without returning names or creating a probe file.
        with os.scandir(descriptor) as entries:
            next(entries, None)
        current = home.lstat()
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ValueError("HOME changed during access verification; review a fresh plan")
        return protection
    except OSError:
        raise ValueError(
            "HOME is locked or unreadable; use the existing local login first"
        ) from None
    finally:
        os.close(descriptor)
