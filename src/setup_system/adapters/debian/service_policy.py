"""Temporarily defer package-triggered service starts under policy-rc.d."""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path

from setup_system.infrastructure.system_files import validate_system_path

DEFER_POLICY = b"#!/bin/sh\n# setupSystem temporary package-stage policy\nexit 101\n"


def check_service_policy(path: Path) -> None:
    validate_system_path(path)
    if path.exists() and path.read_bytes() not in {
        DEFER_POLICY,
        b"#!/bin/sh\nexit 101\n",
        b"#!/bin/sh\nexit 101",
    }:
        raise ValueError("Existing service policy needs review before safe package deferral")
    if (
        path.exists()
        and path.read_bytes() != DEFER_POLICY
        and not path.stat().st_mode & stat.S_IXUSR
    ):
        raise ValueError("Existing service policy is not executable; review it before installation")


@contextlib.contextmanager
def defer_services(path: Path) -> Iterator[None]:
    # Preserve an administrator's policy. A temporary policy prevents SSH/network restarts.
    check_service_policy(path)
    content = DEFER_POLICY
    created = path.exists() and path.read_bytes() == content
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o755)
        created = True
    elif created:
        # Recognized interrupted setupSystem policy; restore its usable executable mode.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, 0o755)
        finally:
            os.close(descriptor)
    try:
        yield
    finally:
        if created:
            validate_system_path(path)
            if path.read_bytes() != content:
                raise RuntimeError("Temporary service policy changed; review it before retrying")
            path.unlink()
