"""Compare and retire recorded entries through pinned directory descriptors."""

import hashlib
import os
import stat
from contextlib import suppress
from pathlib import Path

from setup_system.execution.atomic_rename import rename_new

type Entry = tuple[int, int, int, str]
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
CLAIM_DIRECTORY = ".setup-system-removal-claims"


def _record(parent: int, name: str) -> Entry:
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        content = os.readlink(name, dir_fd=parent)
    elif stat.S_ISREG(info.st_mode):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(name, flags, dir_fd=parent), "rb") as stream:
            if not os.path.samestat(info, os.fstat(stream.fileno())):
                raise ValueError("Program file changed while being inspected")
            content = hashlib.file_digest(stream, "sha256").hexdigest()
    elif stat.S_ISDIR(info.st_mode):
        content = ""
    else:
        raise ValueError("Unexpected special file in program storage")
    return info.st_dev, info.st_ino, info.st_mode, content


def record_tree(root: Path, keep: Path | None = None) -> dict[str, Entry]:
    records = {}
    # fwalk supplies a pinned directory descriptor and never enters symlinked directories.
    checkpoint = keep is not None and keep.is_file()
    for parent, dirs, files, fd in os.fwalk(root, follow_symlinks=False):
        if Path(parent) == root and CLAIM_DIRECTORY in dirs:
            if not checkpoint:
                raise ValueError("Program uses a reserved retirement directory")
            dirs.remove(CLAIM_DIRECTORY)
        for name in dirs + files:
            path = Path(parent) / name
            if path != keep:
                records[str(path.relative_to(root))] = _record(fd, name)
    return records


def _discard(claims: int, name: str, record: Entry) -> None:
    if stat.S_ISDIR(record[2]):
        os.rmdir(name, dir_fd=claims)
    else:
        os.unlink(name, dir_fd=claims)
    os.fsync(claims)


def remove_recorded(root: Path, records: dict[str, Entry], keep: Path | None = None) -> None:
    """Claim each entry without overwrite; retain a racing replacement on disk."""
    selector = {hashlib.sha256(os.fsencode(k)).hexdigest(): k for k in records}
    root_fd = os.open(root, _DIR)
    try:
        with suppress(FileExistsError):
            os.mkdir(CLAIM_DIRECTORY, 0o700, dir_fd=root_fd)
        claims = os.open(CLAIM_DIRECTORY, _DIR, dir_fd=root_fd)
        try:
            info = os.fstat(claims)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Unsafe retirement directory")
            for name in os.listdir(claims):
                key = selector.get(name)
                if key is None or _record(claims, name) != records[key]:
                    raise ValueError("A changed program entry was retained for review")
                _discard(claims, name, records[key])

            def clear(directory: int, prefix: Path) -> None:
                for name in os.listdir(directory):
                    if not prefix.parts and (name == CLAIM_DIRECTORY or root / name == keep):
                        continue
                    relative = prefix / name
                    expected = records.get(str(relative))
                    if expected is None or _record(directory, name) != expected:
                        raise ValueError("A program entry changed during removal")
                    if stat.S_ISDIR(expected[2]):
                        child = os.open(name, _DIR, dir_fd=directory)
                        try:
                            opened = os.fstat(child)
                            if (opened.st_dev, opened.st_ino) != expected[:2]:
                                raise ValueError("Program directory changed during removal")
                            clear(child, relative)
                        finally:
                            os.close(child)
                    claim = hashlib.sha256(os.fsencode(relative)).hexdigest()
                    rename_new(directory, name, claims, claim)
                    os.fsync(directory)
                    os.fsync(claims)
                    try:
                        if _record(claims, claim) != expected:
                            raise ValueError("A racing program edit was preserved")
                        _discard(claims, claim, expected)
                    except (ValueError, OSError):
                        with suppress(FileExistsError, FileNotFoundError):
                            rename_new(claims, claim, directory, name)
                        raise

            clear(root_fd, Path())
        finally:
            os.close(claims)
        os.rmdir(CLAIM_DIRECTORY, dir_fd=root_fd)
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
