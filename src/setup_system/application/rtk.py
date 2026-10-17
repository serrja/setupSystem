"""Detect and maintain the pinned Rust Token Killer binary without PATH duplication."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from setup_system.storage import atomic_write, new_backup, state_root


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


def _target_name(manifest: dict[str, str]) -> str:
    if platform.system() != "Linux":
        raise ValueError("RTK provider supports Linux only")
    architecture = platform.machine()
    key = {"x86_64": "linux_x86_64", "aarch64": "linux_arm64"}.get(architecture)
    if key is None:
        raise ValueError(f"RTK has no pinned target for {architecture}")
    return manifest[key]


def _download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=30) as response, destination.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"RTK download returned HTTP {response.status}")
        shutil.copyfileobj(response, output)


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
    archive_name = _target_name(manifest)
    release = manifest["release"]
    with tempfile.TemporaryDirectory(prefix="setup-rtk-") as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / archive_name
        checksums = temporary_dir / "checksums.txt"
        _download(f"{release}/{archive_name}", archive)
        _download(f"{release}/{manifest['checksums']}", checksums)
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
        backup = new_backup([target])
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, binary.read_bytes(), 0o755, preserve_existing_mode=False)
        record = {
            "backup_id": backup.name,
            "path": str(target),
            "installed_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "version": manifest["version"],
            "archive_sha256": expected.lower(),
            "source": f"{release}/{archive_name}",
        }
        record_path = state_root() / "rtk-installs" / f"{backup.name}.json"
        atomic_write(record_path, (json.dumps(record, indent=2) + "\n").encode())
        return "changed: verified official RTK archive installed", backup


def restore_rtk(backup_id: str) -> str:
    """Restore the prior binary only when the managed one is still unchanged."""
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", backup_id):
        raise ValueError("Invalid RTK backup ID")
    record = json.loads((state_root() / "rtk-installs" / f"{backup_id}.json").read_text())
    target = Path(record["path"])
    if not target.is_relative_to(Path.home().resolve()) or target.is_symlink():
        raise ValueError("Unsafe RTK restore target")
    if hashlib.sha256(target.read_bytes()).hexdigest() != record["installed_sha256"]:
        raise ValueError("RTK changed after apply; review before restoring")
    manifest = json.loads((state_root() / "backups" / backup_id / "manifest.json").read_text())
    items = [item for item in manifest["items"] if item["source"] == str(target)]
    if len(items) != len(manifest["items"]):
        raise ValueError("RTK backup contains unexpected files")
    if items:
        item = items[0]
        saved = state_root() / "backups" / backup_id / item["backup"]
        content = saved.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("RTK backup checksum mismatch")
        atomic_write(target, content, int(item["mode"]), preserve_existing_mode=False)
    else:
        target.unlink()
    return "restored"
