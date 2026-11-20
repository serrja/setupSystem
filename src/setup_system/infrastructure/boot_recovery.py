"""Verified, private backups and compare-and-swap recovery for boot files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from setup_system import installation as system
from setup_system.infrastructure.trusted_bootstrap import protected


def digest(path: Path) -> str | None:
    protected(path, directory=False)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"Unsupported boot file: {path}")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_verified(source: Path, destination: Path, mode: int) -> None:
    protected(destination, directory=False)
    expected = digest(source)
    if expected is None:
        raise ValueError("Missing boot file to copy")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(mode)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        if digest(temporary) != expected:
            raise RuntimeError("Boot file copy verification failed")
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class BootRecovery:
    def __init__(self, directory: Path, kernel: str, paths: tuple[Path, ...]) -> None:
        self.journal = directory / "operation.json"
        self.kernel = kernel
        self.originals: dict[Path, tuple[Path, int] | None] = {}
        self.before: dict[Path, str | None] = {}
        self.after: dict[Path, str] = {}
        self.verified: set[Path] = set()
        for index, path in enumerate(paths):
            self.before[path] = digest(path)
            if self.before[path] is None:
                self.originals[path] = None
            else:
                saved = directory / str(index)
                copy_verified(path, saved, 0o600)
                self.originals[path] = saved, stat.S_IMODE(path.stat().st_mode)
        self.checkpoint("prepared")

    def checkpoint(self, status: str) -> None:
        files = {
            str(path): {
                "backup": str(old[0]) if old else None,
                "mode": old[1] if old else None,
                "sha256": self.before[path],
                "after": self.after.get(path),
                "verified": path in self.verified,
            }
            for path, old in self.originals.items()
        }
        record = {"schema_version": 1, "kernel": self.kernel, "status": status, "files": files}
        system._write_system(self.journal, json.dumps(record).encode())

    def intend(self, path: Path, expected: str) -> None:
        if digest(path) != self.after.get(path, self.before[path]):
            raise ValueError(f"Boot file changed during preparation: {path}")
        self.after[path] = expected
        self.verified.discard(path)
        self.checkpoint("applying")

    def verify(self, path: Path) -> None:
        if digest(path) != self.after[path]:
            raise ValueError(f"Boot file readback failed: {path}")
        self.verified.add(path)
        self.checkpoint("applying")

    def restore(self, *, packages_attempted: bool) -> None:
        self.checkpoint("restoring")
        conflicts = []
        for path, expected in reversed(self.after.items()):
            current = digest(path)
            if current == self.before[path]:
                continue
            if current != expected:
                conflicts.append(path)
                continue
            prior = self.originals[path]
            if prior:
                copy_verified(prior[0], path, prior[1])
            else:
                path.unlink()
            if digest(path) != self.before[path]:
                conflicts.append(path)
        self.checkpoint("partial" if conflicts or packages_attempted else "restored")
        if conflicts:
            raise RuntimeError("Boot recovery preserved later edits; inspect " + str(self.journal))
