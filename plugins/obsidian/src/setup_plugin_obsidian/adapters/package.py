"""Verify the registered Obsidian CLI and desktop launcher against their package."""

import hashlib
import os
import stat
from pathlib import Path

from setup_system_sdk.files.backend import command
from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.files.user_storage import FileSnapshot, private_directory

LAUNCHER = Path("/usr/share/applications/md.obsidian.Obsidian.desktop")


def package_matches(data: bytes, member: str) -> bool:
    # Read the file digest from dpkg's installed control data for this exact package.
    status, rows = command(
        "/usr/bin/dpkg-query", "--control-show", "obsidian", "md5sums", capture=True
    )
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
    return status == 0 and any(
        row.split(maxsplit=1) == [digest, member] for row in rows.splitlines()
    )


def registered_cli() -> Path | None:
    path = Path.home() / ".local/bin/obsidian"
    try:
        with private_directory(path.parent, create=False) as parent:
            saved = FileSnapshot.read(parent, path.name, limit=1024 * 1024)
    except (OSError, ValueError):
        return None
    if saved.mode & stat.S_IXUSR and package_matches(saved.data, "opt/Obsidian/obsidian-cli"):
        return path
    return None


def launcher_available() -> bool:
    protected(LAUNCHER.parent)
    try:
        fd = os.open(LAUNCHER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, PermissionError):
        return False
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        # The upstream Debian launcher is root:root 0664.
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_mode & stat.S_IWOTH
            or os.access(LAUNCHER, os.W_OK)
            or info.st_nlink != 1
            or info.st_size > 64 * 1024
        ):
            return False
        return package_matches(stream.read(64 * 1024 + 1), str(LAUNCHER).lstrip("/"))
