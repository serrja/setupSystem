"""Temporarily defer package-triggered service starts under policy-rc.d."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

from setup_system.infrastructure.atomic_rename import move_new
from setup_system.infrastructure.system_files import set_new_directory_mode, validate_system_path
from setup_system.infrastructure.system_publication import ObservedFile, observe_file

DEFER_POLICY = b"#!/bin/sh\n# setupSystem temporary package-stage policy\nexit 101\n"
ADMIN_POLICIES = {b"#!/bin/sh\nexit 101\n", b"#!/bin/sh\nexit 101"}


def _review(path: Path) -> ObservedFile | None:
    before = observe_file(path)
    if before is not None and before.data != DEFER_POLICY:
        if before.data not in ADMIN_POLICIES:
            raise ValueError("Existing service policy needs review before safe package deferral")
        if not before.mode & stat.S_IXUSR:
            raise ValueError(
                "Existing service policy is not executable; review it before installation"
            )
    return before


def _directory(path: Path) -> None:
    validate_system_path(path, is_directory=True)


def _release(path: Path, approved: ObservedFile) -> None:
    area = Path(tempfile.mkdtemp(prefix=".setup-system-policy-", dir=path.parent))
    set_new_directory_mode(area, 0o700)
    claimed = area / "displaced"
    try:
        move_new(path, claimed, check_directory=_directory)
        try:
            if observe_file(claimed) != approved:
                raise RuntimeError("Temporary service policy changed; review it before retrying")
        except BaseException:
            try:
                move_new(claimed, path, check_directory=_directory)
            except FileExistsError as exc:
                raise RuntimeError(
                    f"Service policy changed concurrently; retained {claimed}"
                ) from exc
            raise
        claimed.unlink()
    finally:
        if not any(area.iterdir()):
            area.rmdir()


@contextlib.contextmanager
def defer_services(path: Path) -> Iterator[None]:
    before = _review(path)
    if before is not None and before.data != DEFER_POLICY:
        yield
        return
    flags = os.O_NOFOLLOW | os.O_CLOEXEC
    if before is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | flags, 0o600)
    else:
        descriptor = os.open(path, os.O_RDONLY | flags)
    try:
        handle = os.fstat(descriptor)
        if before is None:
            if os.write(descriptor, DEFER_POLICY) != len(DEFER_POLICY):
                raise OSError("Could not write the complete temporary service policy")
        elif (handle.st_dev, handle.st_ino) != (before.device, before.inode) or os.pread(
            descriptor, len(DEFER_POLICY) + 1, 0
        ) != DEFER_POLICY:
            raise ValueError("Service policy changed during its review")
        os.fchmod(descriptor, 0o755)
        os.fsync(descriptor)
        approved = observe_file(path)
        if (
            approved is None
            or (approved.device, approved.inode) != (handle.st_dev, handle.st_ino)
            or approved.data != DEFER_POLICY
        ):
            raise ValueError("Service policy changed before package execution")
        try:
            yield
        finally:
            _release(path, approved)
    finally:
        os.close(descriptor)
