"""Validate personal manager paths before inspecting metadata or invoking a package manager."""

import os
import stat
from pathlib import Path


def personal_path(
    home: Path, path: Path, *, regular: bool = False, max_bytes: int | None = 4_000_000
) -> None:
    if not home.is_absolute() or not path.is_relative_to(home) or ".." in path.parts:
        raise ValueError("User tool paths must stay inside HOME")
    if any(parent.is_symlink() for parent in (home, *home.parents)):
        raise ValueError("HOME cannot traverse symlinks")
    parents = (
        home,
        *(
            home.joinpath(*path.relative_to(home).parts[:i])
            for i in range(1, len(path.relative_to(home).parts) + 1)
        ),
    )
    for parent in parents:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            return
        correct_type = (
            stat.S_ISREG(info.st_mode) if regular and parent == path else stat.S_ISDIR(info.st_mode)
        )
        if not correct_type or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError(f"User tool path has unsafe ownership, permissions or type: {parent}")
        if (
            regular
            and parent == path
            and ((max_bytes is not None and info.st_size > max_bytes) or info.st_nlink != 1)
        ):
            raise ValueError(f"Unsafe user package metadata: {path}")
