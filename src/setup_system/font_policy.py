"""Shared stdlib font archive identity for user downloads and privileged preflight."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast


def font_cache_name(version: str, digest: str) -> str:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version):
        raise ValueError("Invalid pinned font version")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid pinned font digest")
    return f"JetBrainsMono-{version}-{digest}.zip"


@dataclass(frozen=True)
class FontFile:
    name: str
    sha256: str


@dataclass(frozen=True)
class FontArchive:
    version: str
    url: str
    sha256: str
    files: tuple[FontFile, ...]
    metadata: dict[str, object] = field(default_factory=dict, repr=False)

    def as_manifest(self) -> dict[str, object]:
        return {
            **self.metadata,
            "version": self.version,
            "url": self.url,
            "sha256": self.sha256,
            "files": [{"name": item.name, "sha256": item.sha256} for item in self.files],
        }

    def preflight_paths(self) -> tuple[str, ...]:
        font_root = f"data:fonts/JetBrainsMonoNerdFont-{self.version}/"
        archive = "cache:setup-system/downloads/" + font_cache_name(self.version, self.sha256)
        return (
            font_root,
            *(font_root + item.name for item in self.files),
            archive,
            archive + ".json",
        )


def load_font_archive(root: Path) -> FontArchive:
    path = root / "manifests/fonts.toml"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Pinned font manifest must be a bounded regular file")
    value = tomllib.loads(path.read_text()).get("jetbrains_mono_nerd_font")
    if not isinstance(value, dict):
        raise ValueError("Invalid pinned font manifest")
    version, digest = value.get("version"), value.get("sha256")
    if not isinstance(version, str) or not isinstance(digest, str):
        raise ValueError("Invalid pinned font version or digest")
    font_cache_name(version, digest)
    expected_url = (
        f"https://github.com/ryanoasis/nerd-fonts/releases/download/v{version}/JetBrainsMono.zip"
    )
    if value.get("url") != expected_url:
        raise ValueError("Font archive must use its pinned official release URL")
    files = value.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 16:
        raise ValueError("Invalid pinned font file list")
    names: set[str] = set()
    selected: list[FontFile] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid pinned font file identity")
        name, checksum = item.get("name"), item.get("sha256")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in {"", ".", ".."}
            or "\\" in name
            or any(ord(character) < 32 for character in name)
            or name in names
            or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
        ):
            raise ValueError("Invalid pinned font file identity")
        names.add(name)
        selected.append(FontFile(name, checksum))
    metadata = {
        key: item for key, item in value.items() if key not in {"version", "url", "sha256", "files"}
    }
    return FontArchive(
        version, expected_url, digest, tuple(selected), cast(dict[str, object], metadata)
    )
