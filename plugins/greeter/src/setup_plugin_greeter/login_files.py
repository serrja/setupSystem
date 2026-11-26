"""Protect login destinations and record known system file ownership."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from setup_system.execution.contracts import ContractModel
from setup_system.execution.manifest_types import Digest, FrozenMap
from setup_system.execution.system_facts import FileMode


@dataclass(frozen=True)
class DesktopPaths:
    boundary: Path = Path("/")
    config: Path = Path("/etc/greetd/setup-system.toml")
    unit_dropin: Path = Path("/etc/systemd/system/greetd.service.d/50-setup-system.conf")
    local_unit: Path = Path("/etc/systemd/system/greetd.service")
    vendor_unit: Path = Path("/usr/lib/systemd/system/greetd.service")
    display_manager: Path = Path("/etc/systemd/system/display-manager.service")
    registry: Path = Path("/etc/setup-system/login-users")
    bridge: Path = Path("/usr/local/libexec/setup-system/login-session")
    style: Path = Path("/usr/local/share/setup-system/login/greeter.css")
    receipt: Path = Path("/var/lib/setup-system/desktop.json")
    backups: Path = Path("/var/lib/setup-system/desktop-backups")


class LoginFileState(ContractModel):
    sha256: Digest
    mode: FileMode


class LoginReceipt(ContractModel):
    schema_version: Literal[1] = Field(alias="schema")
    owner: Literal["setupSystem"]
    files: FrozenMap[str, LoginFileState]
    grub_sha256: str | None = Field(None, pattern="^[a-f0-9]{64}$")
    grub_inputs_sha256: str | None = Field(None, pattern="^[a-f0-9]{64}$")


class LoginFiles:
    def __init__(self, paths: DesktopPaths, owner_uid: int) -> None:
        self.paths = paths
        self.owner_uid = owner_uid

    def receipt(self) -> LoginReceipt | None:
        content = self.read(self.paths.receipt)
        if content is None:
            return None
        try:
            return LoginReceipt.model_validate_json(content, strict=True)
        except ValueError as error:
            raise ValueError("Invalid desktop management receipt") from error

    def check_path(self, path: Path, *, leaf_link: bool = False) -> None:
        if not path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in str(path)):
            raise ValueError("Unsafe desktop system path")
        if not path.is_relative_to(self.paths.boundary):
            raise ValueError("Desktop path escapes its trusted boundary")
        for parent in (path, *path.parents):
            if not parent.is_relative_to(self.paths.boundary):
                break
            if not parent.exists() and not parent.is_symlink():
                continue
            details = parent.lstat()
            if stat.S_ISLNK(details.st_mode):
                if parent == path and leaf_link and details.st_uid == self.owner_uid:
                    continue
                raise ValueError("Desktop system path traverses a symlink")
            if details.st_uid != self.owner_uid or details.st_mode & 0o022:
                raise ValueError("Desktop system path is not administrator controlled")
            if parent != path and not stat.S_ISDIR(details.st_mode):
                raise ValueError("Desktop system parent is not a directory")

    def read(self, path: Path, *, limit: int = 1024 * 1024) -> bytes | None:
        self.check_path(path)
        try:
            details = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_size > limit:
            raise ValueError("Desktop system file has an unsafe type, link count or size")
        return path.read_bytes()

    def fingerprint(self, path: Path, *, limit: int = 1024 * 1024) -> str | None:
        content = self.read(path, limit=limit)
        return None if content is None else hashlib.sha256(content).hexdigest()

    def ensure_parent(self, path: Path, *, private: bool) -> None:
        self.check_path(path)
        missing: list[Path] = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            mode_parent = 0o700 if private else 0o755
            directory.mkdir(mode=mode_parent)
            directory.chmod(mode_parent)
        self.check_path(path)

    def write(
        self,
        path: Path,
        content: bytes,
        mode: int,
        *,
        private: bool = False,
        limit: int = 1024 * 1024,
    ) -> None:
        self.check_path(path)
        if len(content) > limit:
            raise ValueError("Managed desktop content exceeds its size limit")
        current = self.read(path, limit=limit)
        if current == content and stat.S_IMODE(path.stat().st_mode) == mode:
            return
        self.ensure_parent(path, private=private)
        descriptor, temporary = tempfile.mkstemp(prefix=".setup-desktop-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def backup(self, path: Path, content: bytes, mode: int) -> Path:
        identity = hashlib.sha256(str(path).encode()).hexdigest()
        digest = hashlib.sha256(content).hexdigest()
        saved = self.paths.backups / identity / digest
        existing = self.read(saved, limit=16 * 1024 * 1024)
        if existing is None:
            self.write(saved, content, 0o600, private=True, limit=16 * 1024 * 1024)
            self.write(
                saved.with_suffix(".json"),
                json.dumps({"path": str(path), "mode": mode, "sha256": digest}).encode() + b"\n",
                0o600,
                private=True,
            )
        elif existing != content:
            raise ValueError("Desktop backup conflicts with its recorded digest")
        return saved

    def prepare_area(self, path: Path, *, private: bool) -> Path:
        self.ensure_parent(path, private=private)
        area = Path(tempfile.mkdtemp(prefix=".setup-system-login-", dir=path.parent))
        area.chmod(0o700)
        return area

    def move_new(self, source: Path, destination: Path) -> None:
        """Move one root-owned name between previously checked directories."""
        from setup_system.execution.atomic_rename import move_new

        move_new(source, destination, check_directory=self.check_path)
