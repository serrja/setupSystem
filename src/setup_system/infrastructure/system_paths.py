"""Check root-controlled boot and system paths before changing them."""

import stat
from pathlib import Path


def protected(path: Path, *, directory: bool = True) -> None:
    for item in reversed((path, *path.parents)):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                or (item != path and not stat.S_ISDIR(info.st_mode))
            ):
                raise ValueError("Privileged resources require root-owned non-writable paths")
    if path.exists():
        if directory and not path.is_dir():
            raise ValueError("Expected a protected directory")
        if not directory and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError("Expected a protected regular file with one link")


def verify_program_tree(root: Path) -> None:
    """Check all packaged recipes and resources before sending a become response."""
    protected(root)
    protected(root / "Makefile", directory=False)
    if not (root / "Makefile").is_file():
        raise ValueError("The installed program has no Makefile")
    directories = [root]
    while directories:
        for entry in directories.pop().iterdir():
            directory = entry.is_dir()
            protected(entry, directory=directory)
            if directory:
                directories.append(entry)
