"""Resolve files in one selected source checkout or installed wheel."""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ResourceLocator:
    root: Path
    resources: Path = field(init=False)

    @classmethod
    def for_root(cls, root: Path | None = None) -> ResourceLocator:
        if root is None:
            package = Path(__file__).resolve().parent
            root = package.parents[1] if package.parent.name == "src" else package
        return cls(root)

    def __post_init__(self) -> None:
        root = self.root.absolute()
        if ".." in root.parts or any(path.is_symlink() for path in (root, *root.parents)):
            raise ValueError("Release root must not traverse a symlink or parent reference")
        source = root / "src/setup_system/resources"
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "resources", source if source.is_dir() else root / "resources")

    def _child(self, directory: Path, name: str) -> Path:
        path = Path(name)
        if (
            path.is_absolute()
            or not path.parts
            or path.as_posix() != name
            or ".." in path.parts
            or ":" in name
            or "\\" in name
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Invalid release resource path")
        result = directory / path
        for parent in (result, *result.parents):
            if parent.is_symlink():
                raise ValueError(f"Release resource traverses a symlink: {parent}")
            if parent == self.root:
                break
        return result

    def manifest(self, name: str) -> Path:
        return self._child(self.root / "manifests", name)

    def resource(self, name: str) -> Path:
        return self._child(self.resources, name)

    def asset(self, name: str) -> Path:
        return self._child(self.root / "assets", name)


def read_toml(path: Path) -> dict[str, object]:
    """Read bounded regular TOML from a path resolved within the verified release."""
    limit = 1024 * 1024
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Release manifest must be a bounded regular file")
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Release manifest exceeds the size limit")
    return tomllib.loads(content.decode("utf-8"))
