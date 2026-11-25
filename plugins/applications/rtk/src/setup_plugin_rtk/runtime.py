"""Install the selected official RTK binary for the current account."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from setup_plugin_rtk.facts import RtkStatus
from setup_system.domain.contracts import ContractModel
from setup_system.domain.receipts import FileState
from setup_system.infrastructure.downloads import GITHUB_RELEASE_HOSTS, DownloadPolicy, read_https
from setup_system.infrastructure.file_publication import observe
from setup_system.infrastructure.managed_files import apply_files
from setup_system.infrastructure.native_target import architecture
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.receipt_repository import ownership

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024 * 1024
REPOSITORY = "rtk-ai/rtk"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Release(ContractModel):
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    archive: Literal["rtk-x86_64-unknown-linux-musl.tar.gz", "rtk-aarch64-unknown-linux-gnu.tar.gz"]
    sha256: Digest
    checksum_sha256: Digest


def _read(url: str, limit: int) -> bytes:
    hosts = (
        frozenset({"api.github.com"})
        if url.startswith("https://api.github.com/")
        else GITHUB_RELEASE_HOSTS
    )
    return read_https(url, DownloadPolicy(hosts, limit, 30, total_timeout=120))


def release(version: str | None = None) -> Release:
    if version and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Invalid RTK release version")
    platform_asset = {
        "amd64": "rtk-x86_64-unknown-linux-musl.tar.gz",
        "arm64": "rtk-aarch64-unknown-linux-gnu.tar.gz",
    }.get(architecture(platform.machine()))
    if platform.system() != "Linux" or not platform_asset:
        raise ValueError("Official RTK requires Linux amd64 or arm64")
    selector = f"tags/v{version}" if version else "latest"
    metadata = json.loads(
        _read(f"https://api.github.com/repos/{REPOSITORY}/releases/{selector}", 2 * 1024 * 1024)
    )
    if not isinstance(metadata, dict):
        raise ValueError("Invalid official RTK release metadata")
    tag = metadata.get("tag_name")
    if (
        not isinstance(tag, str)
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag)
        or metadata.get("draft") is not False
        or metadata.get("prerelease") is not False
        or not metadata.get("published_at")
        or metadata.get("html_url") != f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
        or (version and tag != "v" + version)
    ):
        raise ValueError("Expected the public stable RTK release")
    assets = metadata.get("assets")
    digests: dict[str, str] = {}
    if not isinstance(assets, list):
        raise ValueError("Invalid official RTK asset inventory")
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") not in (
            platform_asset,
            "checksums.txt",
        ):
            continue
        name = asset["name"]
        origin = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        digest = asset.get("digest")
        if (
            name in digests
            or asset.get("browser_download_url") != origin
            or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            raise ValueError("Invalid official RTK asset identity")
        digests[name] = digest[7:]
    if len(digests) != 2:
        raise ValueError("Official RTK release is missing the target archive or checksums")
    return Release.model_validate(
        {
            "version": tag[1:],
            "archive": platform_asset,
            "sha256": digests[platform_asset],
            "checksum_sha256": digests["checksums.txt"],
        }
    )


def _verify_executable(command: Path) -> RtkStatus:
    """Smoke only the binary extracted from verified official release bytes."""
    env = {"HOME": str(command.parent), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    version = run_command(
        [str(command), "--version"],
        env=env,
        discard_stderr=True,
        timeout=10,
        check=False,
    )
    gain = run_command(
        [str(command), "gain", "--help"],
        env=env,
        discard_stderr=True,
        timeout=10,
        check=False,
    )
    match = re.fullmatch(r"rtk (\d+\.\d+\.\d+)", version.stdout.strip())
    valid = version.returncode == 0 and gain.returncode == 0 and "savings" in gain.stdout.lower()
    return RtkStatus(
        path=command.absolute(),
        version=match.group(1) if match else None,
        product_matches=bool(match and valid),
    )


def _released_binary(selected: Release) -> bytes:
    base = f"https://github.com/{REPOSITORY}/releases/download/v{selected.version}"
    checksums = _read(base + "/checksums.txt", MAX_CHECKSUM_BYTES)
    if hashlib.sha256(checksums).hexdigest() != selected.checksum_sha256:
        raise ValueError("Official RTK checksums digest mismatch")
    matches = [
        fields[0]
        for line in checksums.decode().splitlines()
        if len(fields := line.split()) == 2 and fields[1].lstrip("*") == selected.archive
    ]
    if matches != [selected.sha256]:
        raise ValueError("Official RTK checksum for the target archive does not match")
    archive = _read(base + "/" + selected.archive, MAX_ARCHIVE_BYTES)
    if hashlib.sha256(archive).hexdigest() != selected.sha256:
        raise ValueError("RTK archive checksum mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(archive)) as compressed:
        payload = compressed.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("RTK archive exceeds its unpacked size limit")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        members = [row for row in tar if Path(row.name).name == "rtk"]
        if (
            len(members) != 1
            or not members[0].isreg()
            or Path(members[0].name).is_absolute()
            or ".." in Path(members[0].name).parts
            or not 0 < members[0].size <= MAX_ARCHIVE_BYTES
        ):
            raise ValueError("Expected one bounded regular RTK binary")
        member = tar.extractfile(members[0])
        if member is None:
            raise ValueError("RTK binary is absent")
        return member.read(MAX_ARCHIVE_BYTES + 1)


def _installed(selected: Release, binary: bytes | None = None) -> RtkStatus:
    local = Path.home() / ".local/bin/rtk"
    other = shutil.which("rtk")
    target = local if local.exists() or local.is_symlink() else Path(other) if other else None
    if target is None:
        return RtkStatus(path=None, version=None, product_matches=False)
    if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_ARCHIVE_BYTES:
        return RtkStatus(path=target, version=None, product_matches=False)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    matches = (
        hashlib.sha256(binary if binary is not None else _released_binary(selected)).hexdigest()
        == actual
    )
    owned = target == local and ownership().files.get(str(target)) == actual
    return RtkStatus(
        path=target,
        version=selected.version if matches and os.access(target, os.X_OK) else None,
        product_matches=matches or owned,
    )


def plan_rtk(selected: Release) -> tuple[RtkStatus, str]:
    status = _installed(selected)
    return status, status.describe(selected.version)


def install_rtk(
    selected: Release, *, update: bool = False, reviewed: dict[Path, FileState | None] | None = None
) -> tuple[str, Path | None]:
    data = _released_binary(selected)
    status = _installed(selected, data)
    plan = status.describe(selected.version)
    if status.path is not None and not status.product_matches:
        raise ValueError(plan)
    target = status.path or Path.home() / ".local/bin/rtk"
    user_target = target.is_relative_to(Path.home()) and not target.is_symlink()
    before = observe(target) if user_target else None
    if (
        reviewed is not None
        and user_target
        and (target not in reviewed or reviewed[target] != before)
    ):
        raise ValueError("RTK target changed after confirmation")
    if status.version == selected.version:
        return plan, None
    if status.path is not None and not update:
        raise ValueError("Confirm the RTK update first")
    if not user_target:
        raise ValueError("RTK target must be a regular file inside the current home")
    if before and status.path != target:
        raise ValueError("An unmanaged RTK target requires review")
    with tempfile.TemporaryDirectory(prefix="setup-rtk-") as tmp:
        binary = Path(tmp) / "rtk"
        binary.write_bytes(data)
        binary.chmod(0o700)
        verified = _verify_executable(binary)
        if not verified.product_matches or verified.version != selected.version:
            raise ValueError("Downloaded RTK failed product or version verification")
    backup = apply_files({target: data}, modes={target: 0o755}, expected={target: before})
    return "Installed verified official RTK", backup
