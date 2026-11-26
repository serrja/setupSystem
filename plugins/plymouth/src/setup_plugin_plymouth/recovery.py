"""Verified, private backups and compare-and-swap recovery for boot files."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from setup_system.execution import system_files
from setup_system.execution.atomic_rename import move_new
from setup_system.execution.system_paths import protected

from .models import BootFile, BootReceipt, BootStatus


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
        self.after_modes: dict[Path, int] = {}
        self.verified: set[Path] = set()
        self.identities: dict[Path, tuple[int, int]] = {}
        self.before_ids: dict[Path, tuple[int, int]] = {}
        self.apply_areas: dict[Path, Path] = {}
        self.restore_areas: dict[Path, Path] = {}
        for index, path in enumerate(paths):
            try:
                info = path.lstat()
            except FileNotFoundError:
                info = None
            self.before[path] = digest(path)
            if self.before[path] is None:
                self.originals[path] = None
            else:
                saved = directory / str(index)
                copy_verified(path, saved, 0o600)
                if (
                    info is None
                    or not os.path.samestat(info, path.lstat())
                    or (digest(saved) != self.before[path] or info.st_mode != path.lstat().st_mode)
                ):
                    raise ValueError(f"Boot file changed while backing up: {path}")
                self.before_ids[path] = info.st_dev, info.st_ino
                self.originals[path] = saved, stat.S_IMODE(info.st_mode)
        self.checkpoint("prepared")

    def checkpoint(self, status: BootStatus) -> None:
        files = {
            path: BootFile(
                backup=old[0] if old else None,
                mode=old[1] if old else None,
                sha256=self.before[path],
                after=self.after.get(path),
                after_mode=self.after_modes.get(path),
                after_device=self.identities[path][0] if path in self.identities else None,
                after_inode=self.identities[path][1] if path in self.identities else None,
                apply_area=self.apply_areas.get(path),
                restore_area=self.restore_areas.get(path),
                verified=path in self.verified,
            )
            for path, old in self.originals.items()
        }
        record = BootReceipt(kernel=self.kernel, status=status, files=files)
        system_files.write_system(self.journal, record.model_dump_json().encode())

    def publish(self, path: Path, source: bytes | Path, mode: int) -> None:
        original = self.originals[path]
        before = self.after.get(path, self.before[path])
        before_mode = self.after_modes.get(path, original[1] if original else None)
        before_id = self.identities.get(path, self.before_ids.get(path))

        area = Path(tempfile.mkdtemp(prefix=".setup-system-boot-apply-", dir=path.parent))
        area.chmod(0o700)
        self.apply_areas[path] = area
        self.checkpoint("applying")
        staged, held = area / "prepared", area / "displaced"
        expected: str | None
        if isinstance(source, bytes):
            expected = hashlib.sha256(source).hexdigest()
            system_files.write_system(staged, source, mode)
        else:
            expected = digest(source)
            if expected is None:
                raise ValueError("Boot rebuild candidate disappeared")
            copy_verified(source, staged, mode)
        candidate = staged.lstat()
        if (digest(staged), stat.S_IMODE(candidate.st_mode)) != (expected, mode):
            raise ValueError(f"Boot candidate changed during preparation: {path}")

        self.after[path] = expected
        self.after_modes[path] = mode
        self.identities[path] = candidate.st_dev, candidate.st_ino
        self.verified.discard(path)
        self.checkpoint("applying")
        if before is not None:
            self._claim(path, held, before_id, before, before_mode)
        move_new(staged, path, check_directory=protected)
        self.verify(path)

    def verify(self, path: Path) -> None:
        observed = path.lstat()
        identity = (observed.st_dev, observed.st_ino)
        if (
            self.identities.get(path, identity) != identity
            or digest(path) != self.after[path]
            or stat.S_IMODE(observed.st_mode) != self.after_modes[path]
        ):
            raise ValueError(f"Boot file readback failed: {path}")
        self.identities[path] = identity
        self.verified.add(path)
        self.checkpoint("applying")

    @staticmethod
    def _claim(
        path: Path,
        held: Path,
        identity: tuple[int, int] | None,
        expected: str,
        mode: int | None,
    ) -> None:
        move_new(path, held, check_directory=protected)
        try:
            actual = held.lstat()
            if (actual.st_dev, actual.st_ino) != identity or (
                digest(held),
                stat.S_IMODE(actual.st_mode),
            ) != (expected, mode):
                raise ValueError(f"A later boot edit was preserved: {path}")
        except BaseException:
            # Return even an unreadable, newly protected or non-regular inode.
            # If another writer has already filled the name, retain both versions.
            move_new(held, path, check_directory=protected)
            raise

    def _restore_one(self, path: Path, expected: str) -> None:
        prior = self.originals[path]
        before = (self.before[path], prior[1] if prior else None)
        current = digest(path)
        mode = None if current is None else stat.S_IMODE(path.lstat().st_mode)
        if (current, mode) == before:
            return
        identity = self.identities.get(path)
        apply_area = self.apply_areas.get(path)
        if current is None and apply_area is not None and (apply_area / "prepared").exists():
            displaced = apply_area / "displaced"
            if displaced.exists() or displaced.is_symlink():
                move_new(displaced, path, check_directory=protected)
                if (digest(path), stat.S_IMODE(path.lstat().st_mode)) == before:
                    return
                raise ValueError(f"Displaced boot file returned for review: {path}")
        if (current, mode) != (expected, self.after_modes[path]) or identity is None:
            raise ValueError(f"Boot destination changed: {path}")
        area = Path(tempfile.mkdtemp(prefix=".setup-system-boot-restore-", dir=path.parent))
        area.chmod(0o700)
        self.restore_areas[path] = area
        staged = area / "prepared"
        held = area / "displaced"
        if prior:
            if digest(prior[0]) != before[0]:
                raise ValueError(f"Boot backup changed: {prior[0]}")
            copy_verified(prior[0], staged, prior[1])
            if (digest(staged), stat.S_IMODE(staged.lstat().st_mode)) != before:
                raise ValueError(f"Restored boot candidate differs from its original: {path}")
        self.checkpoint("restoring")
        self._claim(path, held, identity, expected, self.after_modes[path])
        if prior:
            move_new(staged, path, check_directory=protected)
        observed = digest(path)
        observed_mode = None if observed is None else stat.S_IMODE(path.lstat().st_mode)
        if (observed, observed_mode) != before:
            raise ValueError(f"Restored boot contents or mode differ: {path}")

    def restore(self, *, packages_attempted: bool) -> None:
        self.checkpoint("restoring")
        conflicts = []
        for path, expected in reversed(self.after.items()):
            try:
                self._restore_one(path, expected)
            except (ValueError, RuntimeError, OSError) as error:
                conflicts.append(str(error))
        self.checkpoint("partial" if conflicts or packages_attempted else "restored")
        if conflicts:
            raise RuntimeError("Boot recovery preserved later edits; inspect " + str(self.journal))
