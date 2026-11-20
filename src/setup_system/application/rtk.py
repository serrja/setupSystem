"""Detect and maintain the pinned Rust Token Killer binary without PATH duplication."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from setup_system.capabilities import load_policy
from setup_system.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadError,
    DownloadFailure,
    DownloadPolicy,
    ProxyPolicy,
    copy_https,
)
from setup_system.infrastructure.file_receipts import observe
from setup_system.infrastructure.user_files import apply_files

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RtkStatus:
    """Observed binary identity and local installation path."""

    path: Path | None
    version: str | None
    product_matches: bool


def inspect_rtk(path: Path | None = None) -> RtkStatus:
    """Identify rtk-ai/rtk using both version and its distinctive gain command."""
    command = path or (Path(found) if (found := shutil.which("rtk")) else None)
    if command is None or not command.is_file() or not os.access(command, os.X_OK):
        return RtkStatus(None, None, False)
    version = subprocess.run(
        [str(command), "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    gain = subprocess.run(
        [str(command), "gain", "--help"], capture_output=True, text=True, timeout=10, check=False
    )
    match = re.fullmatch(r"rtk (\d+\.\d+\.\d+)", version.stdout.strip())
    valid = version.returncode == 0 and gain.returncode == 0 and "savings" in gain.stdout.lower()
    return RtkStatus(command.absolute(), match.group(1) if match else None, bool(match and valid))


def _manifest(root: Path) -> dict[str, str]:
    with (root / "manifests/rtk.toml").open("rb") as source:
        data = tomllib.load(source)
    if data.get("product") != "rtk-ai/rtk" or not re.fullmatch(
        r"\d+\.\d+\.\d+", data.get("version", "")
    ):
        raise ValueError("RTK manifest has an invalid product or version")
    return data


def plan_rtk(root: Path) -> tuple[RtkStatus, str]:
    """Report the pinned provider action without network or filesystem writes."""
    manifest = _manifest(root)
    status = inspect_rtk()
    if status.path is not None and not status.product_matches:
        return status, "blocked: command named rtk is not rtk-ai/rtk"
    if status.version == manifest["version"]:
        return status, "unchanged: pinned RTK is already available"
    if status.path is not None:
        return status, f"update available: installed {status.version}, pinned {manifest['version']}"
    return status, f"install available: pinned {manifest['version']}"


def _target_name(manifest: dict[str, str], *, root: Path | None = None) -> str:
    if platform.system() != "Linux":
        raise ValueError("RTK provider supports Linux only")
    architecture = platform.machine()
    canonical = load_policy(root).normalize_architecture(architecture)
    key = "linux_" + canonical
    if key not in manifest:
        raise ValueError(f"RTK has no pinned target for {architecture}")
    return manifest[key]


def _download(url: str, destination: Path, *, max_bytes: int = MAX_ARCHIVE_BYTES) -> None:
    policy = DownloadPolicy(
        GITHUB_RELEASE_HOSTS,
        max_bytes,
        30,
        proxy=ProxyPolicy.from_loopback_environment(os.environ),
    )
    try:
        with destination.open("wb") as output:
            copy_https(url, output, policy)
    except DownloadError as exc:
        if exc.kind == DownloadFailure.HTTP:
            raise RuntimeError(f"RTK download returned HTTP {exc.status}") from None
        raise RuntimeError("RTK download failed: " + str(exc)) from None


def install_rtk(root: Path, *, update: bool = False) -> tuple[str, Path | None]:
    """Keep a suitable install, or replace one verified target atomically."""
    manifest = _manifest(root)
    status, plan = plan_rtk(root)
    if status.path is not None and not status.product_matches:
        raise ValueError(plan)
    if status.version == manifest["version"]:
        return plan, None
    if status.path is not None and not update:
        raise ValueError("Use --update to replace a different RTK version")
    target = status.path or Path.home() / ".local/bin/rtk"
    home = Path.home().resolve()
    if not target.is_relative_to(home) or target.is_symlink():
        raise ValueError("RTK target must be a regular file inside the current home")
    if target.exists() and (not target.is_file() or target.stat().st_uid != os.getuid()):
        raise ValueError("RTK target is not an owned regular file")
    before = observe(target)
    if before and status.path != target:
        raise ValueError("An unmanaged RTK target requires review")
    archive_name = _target_name(manifest, root=root)
    release = manifest["release"]
    with tempfile.TemporaryDirectory(prefix="setup-rtk-") as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / archive_name
        checksums = temporary_dir / "checksums.txt"
        _download(f"{release}/{archive_name}", archive)
        _download(f"{release}/{manifest['checksums']}", checksums, max_bytes=MAX_CHECKSUM_BYTES)
        expected = {
            columns[1].lstrip("*"): columns[0]
            for line in checksums.read_text().splitlines()
            if len(columns := line.split()) == 2
        }.get(archive_name)
        if expected is None or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError("Official RTK checksum for the target archive is absent")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected.lower():
            raise ValueError("RTK archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as bundle:
            members = [
                member
                for member in bundle.getmembers()
                if member.name.split("/")[-1] == "rtk" and member.isfile()
            ]
            if len(members) != 1:
                raise ValueError("RTK archive must contain exactly one regular binary")
            stream = bundle.extractfile(members[0])
            if stream is None:
                raise ValueError("RTK binary could not be read")
            binary = temporary_dir / "rtk"
            with binary.open("wb") as output:
                shutil.copyfileobj(stream, output)
        binary.chmod(0o700)
        verified = inspect_rtk(binary)
        if not verified.product_matches or verified.version != manifest["version"]:
            raise ValueError("Downloaded RTK executable failed product/version verification")
        backup = apply_files(
            {target: binary.read_bytes()},
            modes={target: 0o755},
            expected={target: before.sha256 if before else None},
        )
        return "changed: verified official RTK archive installed", backup
