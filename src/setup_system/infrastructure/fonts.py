"""Verify, cache and render the selected font profile without root privileges."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from setup_system.domain.displays import MonitorLayout
from setup_system.domain.settings import ThemeSettings
from setup_system.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadError,
    DownloadFailure,
    DownloadPolicy,
    ProxyPolicy,
    read_https,
)
from setup_system.font_policy import font_cache_name, load_font_archive
from setup_system.infrastructure.theme import render
from setup_system.infrastructure.user_files import changed_digests

# The pinned v3.4.0 archive is 129,115,244 bytes; keep downloads and cache reads bounded.
MAX_FONT_ARCHIVE_BYTES = 128 * 1024 * 1024


class FontPolicy(BaseModel):
    """Validated values used by every generated font asset and desktop setting."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    family: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]+$")
    emoji_fallback: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]+$")
    ui_size: int = Field(ge=6, le=48)
    terminal_size: int = Field(ge=6, le=48)
    panel_size: int = Field(ge=6, le=64)
    previous_fontconfig_sha256: tuple[str, ...] = ()

    def render(
        self,
        source: Path,
        theme: ThemeSettings | None = None,
        *,
        keyboard: str | None = None,
        monitors: tuple[MonitorLayout, ...] = (),
    ) -> bytes:
        return render(
            source.read_text(), theme, keyboard=keyboard, monitors=monitors, fonts=self
        ).encode()


def font_policy(root: Path) -> FontPolicy:
    """Read the one font-policy manifest; do not download or apply anything."""
    manifest = tomllib.loads((root / "manifests/fonts.toml").read_text())
    font = manifest["jetbrains_mono_nerd_font"]
    return FontPolicy(
        family=font["family"], emoji_fallback=font["emoji_fallback"], **manifest["profile"]
    )


def _safe(path: Path) -> Path:
    home = Path.home().resolve()
    path = path.expanduser().absolute()
    if not path.is_relative_to(home) or ".." in path.parts:
        raise ValueError("Profile path must stay inside the user's home")
    for parent in (path, *path.parents):
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f"Profile path traverses symlink: {parent}")
    return path


def _xdg(name: str, default: str) -> Path:
    return _safe(Path(os.environ.get(name, Path.home() / default)))


def _font_manifest(root: Path) -> dict[str, object]:
    return load_font_archive(root).as_manifest()


def _font_cache_path(path: Path) -> Path:
    result = _safe(path)
    home = Path.home().resolve()
    for current in (result, *result.parents):
        if current.exists():
            info = current.lstat()
            if info.st_uid != os.geteuid():
                raise ValueError("Font cache path has a different owner")
            if current != result and not current.is_dir():
                raise ValueError("Font cache parent is not a directory")
        if current == home:
            break
    return result


def _font_cache(
    manifest: dict[str, object],
) -> tuple[Path, Path, bytes]:
    folder = _xdg("XDG_CACHE_HOME", ".cache") / "setup-system/downloads"
    name = font_cache_name(str(manifest["version"]), str(manifest["sha256"]))
    archive = _font_cache_path(folder / name)
    receipt = _font_cache_path(archive.with_suffix(".zip.json"))
    provenance = {
        "schema": 1,
        "owner": "setupSystem",
        "kind": "font-archive",
        "version": manifest["version"],
        "url": manifest["url"],
        "sha256": manifest["sha256"],
    }
    return archive, receipt, (json.dumps(provenance, sort_keys=True) + "\n").encode()


def _font_archive(path: Path, checksum: str) -> bytes:
    _font_cache_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_size > MAX_FONT_ARCHIVE_BYTES
        ):
            raise ValueError("Font cache must be a bounded user-owned regular file")
        content = stream.read(MAX_FONT_ARCHIVE_BYTES + 1)
    if len(content) > MAX_FONT_ARCHIVE_BYTES or hashlib.sha256(content).hexdigest() != checksum:
        raise ValueError("Font cache archive digest mismatch; preserve it and review the cache")
    return content


def _font_contents(content: bytes, manifest: dict[str, object]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            if len(bundle.infolist()) > 4096:
                raise ValueError("Font archive contains too many entries")
            total = 0
            for item in cast(list[dict[str, str]], manifest["files"]):
                matches = [entry for entry in bundle.infolist() if entry.filename == item["name"]]
                if len(matches) != 1:
                    raise ValueError("Font archive has missing or duplicate managed files")
                entry = matches[0]
                mode = stat.S_IFMT(entry.external_attr >> 16)
                total += entry.file_size
                if (
                    entry.is_dir()
                    or mode not in {0, stat.S_IFREG}
                    or entry.file_size > 16 * 1024 * 1024
                    or total > 64 * 1024 * 1024
                ):
                    raise ValueError("Font archive member is unsafe or exceeds its size limit")
                data = bundle.read(entry)
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("Font file digest mismatch")
                result[item["name"]] = data
    except zipfile.BadZipFile:
        raise ValueError("Invalid cached font archive") from None
    return result


def _font_publish(path: Path, content: bytes) -> None:
    _font_cache_path(path)
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    for current in reversed(missing):
        current.mkdir(mode=0o700)
        current.chmod(0o700)
    _font_cache_path(path)
    descriptor, name = tempfile.mkstemp(prefix=".font-download-", dir=path.parent)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(candidate, path, follow_symlinks=False)
    finally:
        candidate.unlink(missing_ok=True)


def prefetch_fonts(root: Path) -> None:
    """Cache a pinned font archive without requiring installed graphical commands."""
    if os.geteuid() == 0:
        raise ValueError("Workstation prefetch must run as the target user")
    manifest = _font_manifest(root)
    archive, receipt, provenance = _font_cache(manifest)
    if receipt.exists() and (
        not receipt.is_file()
        or receipt.stat().st_size > 32 * 1024
        or receipt.read_bytes() != provenance
    ):
        raise ValueError("Font cache provenance differs; preserve it for review")
    if archive.exists():
        content = _font_archive(archive, str(manifest["sha256"]))
    else:
        policy = DownloadPolicy(
            GITHUB_RELEASE_HOSTS,
            MAX_FONT_ARCHIVE_BYTES,
            60,
            ProxyPolicy.from_loopback_environment(os.environ),
        )
        try:
            content = read_https(str(manifest["url"]), policy)
        except DownloadError as exc:
            if exc.kind == DownloadFailure.SIZE:
                raise ValueError("Font archive exceeds its size limit") from None
            raise ValueError(f"Font download failed: {exc}") from None
        if hashlib.sha256(content).hexdigest() != manifest["sha256"]:
            raise ValueError("Font archive digest mismatch")
    _font_contents(content, manifest)
    if not archive.exists():
        _font_publish(archive, content)
    if not receipt.exists():
        _font_publish(receipt, provenance)


def _installed_targets(archive: dict[str, object]) -> dict[Path, str]:
    data = _safe(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
    destination = _safe(data / ("fonts/JetBrainsMonoNerdFont-" + str(archive["version"])))
    return {
        _safe(destination / item["name"]): item["sha256"]
        for item in cast(list[dict[str, str]], archive["files"])
    }


def font_drift(root: Path) -> tuple[Path, ...]:
    return changed_digests(_installed_targets(_font_manifest(root)))


def font_files(root: Path, *, offline: bool = False) -> dict[Path, bytes]:
    manifest = _font_manifest(root)
    missing = changed_digests(_installed_targets(manifest))
    if not missing:
        return {}
    if not offline:
        prefetch_fonts(root)
    archive, receipt, provenance = _font_cache(manifest)
    if not archive.is_file() or not receipt.is_file():
        raise ValueError("Verified font cache is missing; prefetch before offline apply")
    if receipt.stat().st_size > 32 * 1024 or receipt.read_bytes() != provenance:
        raise ValueError("Font cache provenance differs; preserve it for review")
    contents = _font_contents(_font_archive(archive, str(manifest["sha256"])), manifest)
    return {path: contents[path.name] for path in missing}
