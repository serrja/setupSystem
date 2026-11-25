"""Write-ahead recovery for managed login files."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictInt

from setup_system.domain.contracts import ContractModel
from setup_system.domain.manifest_types import Digest, FrozenMap
from setup_system.domain.system_facts import FileMode

from .login_files import LoginFiles

MAX_FILE = 16 * 1024 * 1024
Status = Literal["applying", "complete", "restored", "partial"]


class LoginChange(ContractModel):
    backup: Path | None
    before: Digest | None
    before_mode: FileMode | None
    after: Digest
    after_mode: FileMode
    area: Path
    device: StrictInt = Field(ge=0)
    inode: StrictInt = Field(gt=0)


class LoginProgress(ContractModel):
    schema_version: Literal[1] = 1
    previous_target: str
    manager_enabled: StrictBool
    status: Status = "applying"
    files: FrozenMap[Path, LoginChange] = Field(default_factory=dict)


class LoginRecovery:
    def __init__(self, files: LoginFiles, previous_target: str, manager_enabled: bool) -> None:
        self.files = files
        self.path = files.paths.backups / "operation.json"
        self.progress = LoginProgress(
            previous_target=previous_target, manager_enabled=manager_enabled
        )
        prior = files.read(self.path)
        if prior is not None:
            previous = LoginProgress.model_validate_json(prior, strict=True)
            if previous.status in {"partial", "applying"}:
                files.backup(self.path, prior, 0o600)
        self.checkpoint("applying")

    def checkpoint(self, status: Status) -> None:
        self.progress = self.progress.validated(status=status)
        content = self.progress.model_dump_json(indent=2).encode()
        self.files.write(self.path, content, 0o600, private=True)
        if self.files.read(self.path) != content:
            raise RuntimeError("Login progress readback failed")

    def publish(
        self, path: Path, content: bytes, mode: int, expected: str | None, expected_mode: int | None
    ) -> None:
        original = self.files.read(path, limit=MAX_FILE)
        before = None if original is None else hashlib.sha256(original).hexdigest()
        before_mode = None if original is None else stat.S_IMODE(path.stat().st_mode)
        if (before, before_mode) != (expected, expected_mode):
            raise ValueError(f"Login file changed after planning: {path}")
        if original == content and before_mode == mode:
            return
        backup = None
        if original is not None and before_mode is not None:
            backup = self.files.backup(path, original, before_mode)
        area = self.files.prepare_area(path, private=path == self.files.paths.receipt)
        self.files.write(area / "prepared", content, mode, private=True, limit=MAX_FILE)
        staged = (area / "prepared").stat()
        change = LoginChange(
            area=area,
            device=staged.st_dev,
            inode=staged.st_ino,
            backup=backup,
            before=before,
            before_mode=before_mode,
            after=hashlib.sha256(content).hexdigest(),
            after_mode=mode,
        )
        self.progress = self.progress.validated(files={**self.progress.files, path: change})
        self.checkpoint("applying")
        if before is not None:
            self._claim(path, area / "original", before, before_mode)
        self.files.move_new(area / "prepared", path)
        if (
            self.files.fingerprint(path, limit=MAX_FILE) != change.after
            or stat.S_IMODE(path.stat().st_mode) != mode
        ):
            raise RuntimeError(f"Login file readback failed: {path}")

    def _claim(
        self,
        path: Path,
        held: Path,
        expected: str,
        mode: int | None,
        identity: tuple[int, int] | None = None,
    ) -> None:
        self.files.move_new(path, held)
        displaced = held.lstat()
        if (
            (identity is None or (displaced.st_dev, displaced.st_ino) == identity)
            and self.files.fingerprint(held, limit=MAX_FILE) == expected
            and stat.S_IMODE(held.stat().st_mode) == mode
        ):
            return
        try:
            self.files.move_new(held, path)
        except FileExistsError:
            raise ValueError(f"Concurrent changes retained at {path} and {held}") from None
        raise ValueError(f"Later edit preserved at {path}")

    def restore(self) -> list[str]:
        errors = []
        for path, change in reversed(tuple(self.progress.files.items())):
            try:
                current = self.files.fingerprint(path, limit=MAX_FILE)
                mode = None if current is None else stat.S_IMODE(path.stat().st_mode)
                original = change.area / "original"
                if (current, mode) == (change.before, change.before_mode):
                    continue
                if current is None and change.before is not None and original.exists():
                    self.files.move_new(original, path)
                    continue
                info = path.lstat() if current is not None else None
                if (
                    info is None
                    or (info.st_dev, info.st_ino) != (change.device, change.inode)
                    or (current, mode) != (change.after, change.after_mode)
                ):
                    raise ValueError(f"Later edit preserved at {path}")
                if change.before is not None:
                    if (
                        self.files.fingerprint(original, limit=MAX_FILE) != change.before
                        or stat.S_IMODE(original.stat().st_mode) != change.before_mode
                    ):
                        raise ValueError(f"Displaced original changed; inspect {original}")
                self._claim(
                    path,
                    change.area / "restored-after",
                    change.after,
                    change.after_mode,
                    (change.device, change.inode),
                )
                if change.before is not None:
                    self.files.move_new(original, path)
                if self.files.fingerprint(path, limit=MAX_FILE) != change.before:
                    raise RuntimeError(f"Restored login file differs: {path}")
            except (OSError, RuntimeError, ValueError) as error:
                errors.append(str(error))
        return errors
