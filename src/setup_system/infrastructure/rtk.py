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
from pathlib import Path

from setup_system.domain.tool_releases import RtkRelease
from setup_system.domain.workspace_facts import RtkStatus
from setup_system.infrastructure.catalog_repository import load_policy, read_contract
from setup_system.infrastructure.downloads import (
    GITHUB_RELEASE_HOSTS,
    DownloadError,
    DownloadFailure,
    DownloadPolicy,
    ProxyPolicy,
    copy_https,
)
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.resource_locator import ResourceLocator

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024 * 1024


def inspect_rtk(path: Path | None = None) -> RtkStatus:
    """Identify rtk-ai/rtk using both version and its distinctive gain command."""
    command = path or (Path(found) if (found := shutil.which("rtk")) else None)
    if command is None or not command.is_file() or not os.access(command, os.X_OK):
        return RtkStatus(path=None, version=None, product_matches=False)
    version = subprocess.run(
        [str(command), "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    gain = subprocess.run(
        [str(command), "gain", "--help"], capture_output=True, text=True, timeout=10, check=False
    )
    match = re.fullmatch(r"rtk (\d+\.\d+\.\d+)", version.stdout.strip())
    valid = version.returncode == 0 and gain.returncode == 0 and "savings" in gain.stdout.lower()
    return RtkStatus(
        path=command.absolute(),
        version=match.group(1) if match else None,
        product_matches=bool(match and valid),
    )


def plan_rtk(root: Path) -> tuple[RtkStatus, str]:
    """Report the pinned provider action without network or filesystem writes."""
    manifest = read_contract(ResourceLocator.for_root(root).manifest("rtk.toml"), RtkRelease)
    status = inspect_rtk()
    return status, status.describe(manifest.version)


def _target_name(manifest: RtkRelease, *, root: Path | None = None) -> str:
    if platform.system() != "Linux":
        raise ValueError("RTK provider supports Linux only")
    architecture = platform.machine()
    canonical = load_policy(root).normalize_architecture(architecture)
    if canonical == "x86_64":
        return manifest.linux_x86_64
    if canonical == "arm64":
        return manifest.linux_arm64
    raise ValueError(f"RTK has no pinned target for {architecture}")


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
    manifest = read_contract(ResourceLocator.for_root(root).manifest("rtk.toml"), RtkRelease)
    status = inspect_rtk()
    plan = status.describe(manifest.version)
    if status.path is not None and not status.product_matches:
        raise ValueError(plan)
    if status.version == manifest.version:
        return plan, None
    if status.path is not None and not update:
        raise ValueError("Confirm the update before replacing a different RTK version")
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
    release = manifest.release
    with tempfile.TemporaryDirectory(prefix="setup-rtk-") as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / archive_name
        checksums = temporary_dir / "checksums.txt"
        _download(f"{release}/{archive_name}", archive)
        _download(f"{release}/{manifest.checksums}", checksums, max_bytes=MAX_CHECKSUM_BYTES)
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
        if not verified.product_matches or verified.version != manifest.version:
            raise ValueError("Downloaded RTK executable failed product/version verification")
        backup = apply_files(
            {target: binary.read_bytes()},
            modes={target: 0o755},
            expected={target: before.sha256 if before else None},
        )
        return "changed: verified official RTK archive installed", backup
