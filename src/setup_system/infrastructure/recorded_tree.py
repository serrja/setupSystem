"""Inspect and remove exactly the recorded entries of a private program tree."""

import hashlib
import os
import stat
from pathlib import Path


def _record(path: Path) -> tuple[int, int, int, str]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        content = os.readlink(path)
    elif stat.S_ISREG(info.st_mode):
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            if os.fstat(stream.fileno()).st_ino != info.st_ino:
                raise ValueError("Extension changed during removal")
            content = hashlib.file_digest(stream, "sha256").hexdigest()
    elif stat.S_ISDIR(info.st_mode):
        content = ""
    else:
        raise ValueError("Unexpected file in extension storage")
    return info.st_dev, info.st_ino, info.st_mode, content


def record_tree(root: Path, keep: Path | None = None) -> dict[str, tuple[int, int, int, str]]:
    return {str(p.relative_to(root)): _record(p) for p in root.rglob("*") if p != keep}


def remove_recorded(
    root: Path, records: dict[str, tuple[int, int, int, str]], keep: Path | None = None
) -> None:
    # Traverse only existing entries; a missing file is a completed deletion.
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path == keep:
            continue
        if path.parent.resolve() != path.parent:
            raise ValueError("A directory changed during retirement")
        identity = _record(path)
        if records.get(str(path.relative_to(root))) != identity:
            raise ValueError("An extension file was edited during removal")
        if stat.S_ISDIR(identity[2]):
            path.rmdir()
        else:
            path.unlink()
