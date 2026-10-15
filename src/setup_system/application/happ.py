"""Happ package and private state operations; Happ owns its routing and subscriptions."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from setup_system.planning import Platform, detectPlatform
from setup_system.storage import atomicWrite, newBackup, restore, stateRoot


@dataclass(frozen=True)
class HappStatus:
    installed: str
    binaryAvailable: bool | None
    running: bool
    nativeAutostart: str
    loginRegistration: str
    autoConnect: str
    routing: str
    profileCount: int | None


def _run(argv: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _running() -> bool:
    if not shutil.which("pgrep"):
        return False
    return any(_run(["pgrep", "-x", name]).returncode == 0 for name in ("Happ", "happd"))


def _preferences() -> configparser.SectionProxy | None:
    path = Path.home() / ".config/Happ.conf"
    if not path.is_file() or path.is_symlink():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    return parser["Preferences"] if parser.has_section("Preferences") else None


def _setting(preferences: configparser.SectionProxy | None, key: str) -> str:
    if preferences is None:
        return "unknown"
    value = preferences.get(key)
    if value is None:
        return "unknown"
    return {"true": "enabled", "1": "enabled", "false": "disabled", "0": "disabled"}.get(
        value.lower(), "unknown"
    )


def _registration() -> str:
    path = Path.home() / ".config/autostart/Happ.desktop"
    if not path.is_file() or path.is_symlink():
        return "absent"
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return "unknown"
    if not parser.has_section("Desktop Entry"):
        return "unknown"
    entry = parser["Desktop Entry"]
    if entry.get("Hidden", "false").lower() == "true":
        return "disabled"
    if entry.get("X-GNOME-Autostart-enabled", "true").lower() != "true":
        return "disabled"
    executable = entry.get("Exec", "").split(maxsplit=1)[0]
    return "enabled" if Path(executable).name.lower() == "happ" else "unknown"


def inspectHapp(platformAdapter: Platform | None = None) -> HappStatus:
    platformAdapter = platformAdapter or detectPlatform()
    installed = "absent"
    nativeAutostart = "unknown"
    loginRegistration = "unknown"
    autoConnect = "unknown"
    routing = "unknown"
    profileCount: int | None = None
    binaryAvailable: bool | None = None
    if platformAdapter.distribution in {"debian", "ubuntu"}:
        binaryAvailable = shutil.which("happ") is not None
        try:
            result = _run(["dpkg-query", "-W", "-f=${Status} ${Version} ${Architecture}", "happ"])
            if result.returncode == 0 and result.stdout.startswith("install ok installed "):
                parts = result.stdout.split()
                installed = f"deb {parts[3]} {parts[4]}" if len(parts) >= 5 else "deb unknown"
            elif shutil.which("happ"):
                installed = "present from another source"
        except OSError:
            installed = "unknown"
        preferences = _preferences()
        nativeAutostart = _setting(preferences, "advancedsettings\\autostart")
        autoConnect = _setting(preferences, "subscriptions\\subsconnectonopen")
        routing = _setting(preferences, "tunnelsettings\\routing\\userouting")
        loginRegistration = _registration()
        routeFile = Path.home() / ".config/Happ/routing.json"
        if routeFile.is_file() and not routeFile.is_symlink():
            try:
                profiles = json.loads(routeFile.read_text(encoding="utf-8")).get("routings")
                profileCount = len(profiles) if isinstance(profiles, list) else None
            except (OSError, ValueError, AttributeError):
                pass
    elif platformAdapter.distribution == "macos":
        found = [
            path
            for path in (Path("/Applications/Happ.app"), Path.home() / "Applications/Happ.app")
            if path.is_dir()
        ]
        if found:
            installed = "desktop app present; channel and version need native verification"
        binaryAvailable = bool(found)
    return HappStatus(
        installed,
        binaryAvailable,
        _running(),
        nativeAutostart,
        loginRegistration,
        autoConnect,
        routing,
        profileCount,
    )


def formatHappStatus(status: HappStatus) -> str:
    count = "unknown" if status.profileCount is None else str(status.profileCount)
    binaryStatus = (
        "unknown"
        if status.binaryAvailable is None
        else "present"
        if status.binaryAvailable
        else "absent"
    )
    return "\n".join(
        (
            f"package: {status.installed}",
            f"desktop executable: {binaryStatus}",
            f"process: {'running' if status.running else 'not observed'}",
            f"native autostart setting: {status.nativeAutostart}",
            f"login registration: {status.loginRegistration}",
            "next-login launch: needsManualCheck",
            f"automatic connection on open: {status.autoConnect} (preserved)",
            f"routing setting: {status.routing} (preserved)",
            f"routing profile count: {count}",
        )
    )


def _manifest(projectRoot: Path) -> dict[str, object]:
    return tomllib.loads((projectRoot / "manifests/happ.toml").read_text(encoding="utf-8"))


def _download(url: str, sha256: str, target: Path, limit: int) -> None:
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.parent.chmod(0o700)
    candidate = target.with_name(target.name + f".{os.getpid()}.part")
    digest = hashlib.sha256()
    total = 0
    try:
        with urlopen(Request(url, headers={"User-Agent": "setupSystem/0.1"}), timeout=45) as source:
            final = urlsplit(source.geturl())
            if final.scheme != "https" or final.hostname not in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }:
                raise RuntimeError("Unexpected Happ release redirect")
            with candidate.open("wb") as output:
                candidate.chmod(0o600)
                while block := source.read(1024 * 1024):
                    total += len(block)
                    if total > limit:
                        raise RuntimeError("Happ release asset exceeds size limit")
                    digest.update(block)
                    output.write(block)
        if digest.hexdigest() != sha256:
            raise RuntimeError("Happ release digest mismatch")
        os.replace(candidate, target)
    finally:
        candidate.unlink(missing_ok=True)


def setupHapp(projectRoot: Path, platformAdapter: Platform | None = None) -> str:
    """Preserve an installed client; install only a missing verified Linux package."""
    platformAdapter = platformAdapter or detectPlatform()
    if not platformAdapter.implemented:
        return "unsupported: " + platformAdapter.reason
    current = inspectHapp(platformAdapter)
    if current.installed != "absent":
        return "unchanged: " + current.installed
    manifest = _manifest(projectRoot)
    release = str(manifest["version"])
    packageVersion = str(manifest["packageVersion"])
    if platformAdapter.distribution == "macos":
        macos = manifest["macos"]
        if not isinstance(macos, dict) or not isinstance(macos.get("universal"), dict):
            raise ValueError("Invalid Happ macOS release asset")
        asset = macos["universal"]
        target = stateRoot() / "downloads" / f"Happ.macOS.universal-{release}.dmg"
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != asset["sha256"]
        ):
            _download(str(asset["url"]), str(asset["sha256"]), target, 250 * 1024 * 1024)
        return (
            f"needsManualAction: verified official macOS DMG staged at {target}; "
            "confirm the desktop channel and code signature in Finder before installation"
        )
    if platformAdapter.distribution not in {"debian", "ubuntu"}:
        return "unsupported: no Happ package provider"
    sudo = shutil.which("sudo")
    if not sudo or _run([sudo, "-n", "true"]).returncode != 0:
        return "blocked: targeted sudo is unavailable; no download or package change was made"
    linux = manifest["linux"]
    if not isinstance(linux, dict):
        raise ValueError("Invalid Happ release manifest")
    key = "amd64" if platformAdapter.architecture == "x86_64" else "arm64"
    asset = linux[key]
    if not isinstance(asset, dict):
        raise ValueError("Invalid Happ release asset")
    target = stateRoot() / "downloads" / f"happ-{release}-{key}.deb"
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != asset["sha256"]:
        _download(str(asset["url"]), str(asset["sha256"]), target, 200 * 1024 * 1024)
    metadata = _run(["dpkg-deb", "-f", str(target), "Package", "Version", "Architecture"])
    fields = metadata.stdout.splitlines()
    if (
        metadata.returncode != 0
        or len(fields) != 3
        or fields[0] != "happ"
        or fields[1] != packageVersion
        or fields[2] != asset["packageArchitecture"]
    ):
        raise RuntimeError("Happ .deb package metadata differs from the pinned release")
    install = subprocess.run(
        [sudo, "-n", "apt-get", "install", "-y", str(target)], timeout=900, check=False
    )
    if install.returncode != 0:
        raise RuntimeError(f"Happ package installation failed: exit {install.returncode}")
    verified = inspectHapp(platformAdapter)
    if not verified.installed.startswith(f"deb {packageVersion} "):
        raise RuntimeError("Happ package version was not verified after installation")
    return "changed: official Happ .deb installed; GUI, routing and login need separate checks"


def backupHapp() -> Path:
    """Capture Linux desktop settings and SQLite through its online backup API."""
    platformAdapter = detectPlatform()
    if platformAdapter.distribution not in {"debian", "ubuntu"} or not platformAdapter.implemented:
        raise RuntimeError("Native Happ export is required on this platform")
    if _running():
        raise RuntimeError("Happ is running; use its native export or arrange a separate stop")
    home = Path.home()
    paths = [
        home / ".config/Happ.conf",
        home / ".config/Happ/config.json",
        home / ".config/Happ/routing.json",
        home / ".config/autostart/Happ.desktop",
    ]
    routingCache = home / ".local/share/Happ/routing"
    if routingCache.is_dir() and not routingCache.is_symlink():
        paths.extend(
            path for path in routingCache.rglob("*") if path.is_file() and not path.is_symlink()
        )
    database = home / ".config/Happ/subs.db"
    if not database.is_file() or database.is_symlink():
        raise RuntimeError("Happ subscription database is missing or unsafe")
    folder = newBackup(paths)
    destination = folder / "files/.config/Happ/subs.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    sourceConnection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)
    backupConnection = sqlite3.connect(destination, timeout=10)
    try:
        sourceConnection.backup(backupConnection)
        integrity = backupConnection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise RuntimeError("Happ SQLite backup integrity check failed")
    finally:
        backupConnection.close()
        sourceConnection.close()
    destination.chmod(0o600)
    manifestPath = folder / "manifest.json"
    manifest = json.loads(manifestPath.read_text(encoding="utf-8"))
    manifest["application"] = "happ"
    manifest["items"].append(
        {
            "source": str(database),
            "home_relative": ".config/Happ/subs.db",
            "backup": "files/.config/Happ/subs.db",
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "mode": 0o600,
            "kind": "sqliteSnapshot",
        }
    )
    atomicWrite(manifestPath, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    return folder


def previewHappRestore(backupId: str) -> list[tuple[str, str]]:
    return restore(backupId, apply=False)


def classifyLink(value: str, *, routingProfile: bool = False) -> str:
    """Classify locally without fetching or logging a user-supplied link."""
    if not value or len(value) > 16384 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("Invalid or oversized link; its contents were not logged")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError("Invalid link; its contents were not logged") from None
    if routingProfile:
        if parts.scheme != "happ" or parts.netloc != "routing":
            raise ValueError("Expected a Happ routing link; its contents were not logged")
        if parts.path.startswith("/add/") and len(parts.path) > len("/add/"):
            return "routingAdd"
        if parts.path.startswith("/onadd/") and len(parts.path) > len("/onadd/"):
            return "routingActivate"
        raise ValueError("Unsupported routing action; its contents were not logged")
    if parts.scheme in {"http", "https"} and parts.netloc:
        return "webSubscription"
    if parts.scheme == "happ" and parts.netloc.startswith(("crypto", "crypt")):
        return "encryptedSubscription"
    if parts.scheme in {"vless", "vmess", "socks", "trojan", "ss", "hysteria2", "hy2"}:
        return "configuration"
    raise ValueError("Unsupported subscription/configuration link; its contents were not logged")


def clipboardCommand() -> tuple[list[str], list[str]] | None:
    """Only an explicit user action calls this; clipboard contents may enter system history."""
    if detectPlatform().distribution == "macos" and shutil.which("pbcopy"):
        return (["pbcopy"], ["pbcopy"])
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return (["wl-copy"], ["wl-copy", "--clear"])
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        command = ["xclip", "-selection", "clipboard"]
        return (command, command)
    return None


def copyLinkToClipboard(value: str) -> None:
    command = clipboardCommand()
    if command is None:
        raise RuntimeError("No supported clipboard command; enter the link directly in Happ")
    result = subprocess.run(
        command[0], input=value, text=True, capture_output=True, timeout=10, check=False
    )
    if result.returncode != 0:
        raise RuntimeError("Clipboard transfer failed; no link details were logged")


def clearLinkClipboard() -> None:
    command = clipboardCommand()
    if command is None:
        return
    try:
        subprocess.run(
            command[1], input="", text=True, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def restoreHapp(
    backupId: str, *, apply: bool = False, replaceCurrent: bool = False
) -> list[tuple[str, str]]:
    """Restore a reviewed Happ snapshot only while its desktop process is closed."""
    statuses = previewHappRestore(backupId)
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", backupId):
        raise ValueError("Invalid backup ID")
    folder = stateRoot() / "backups" / backupId
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("application") != "happ":
        raise ValueError("Backup is not a Happ snapshot")
    if not apply:
        return statuses
    if _running():
        raise RuntimeError("Happ is running; arrange an explicit stop before restoring")
    database = Path.home() / ".config/Happ/subs.db"
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise RuntimeError(
            "Happ SQLite WAL/SHM exists; use native export or resolve with app closed"
        )
    if not replaceCurrent and any(
        status not in {"unchanged", "would-create"} for _, status in statuses
    ):
        raise RuntimeError("Existing Happ state is preserved; review and use --replaceCurrent")
    allowed = {
        ".config/Happ.conf",
        ".config/Happ/config.json",
        ".config/Happ/routing.json",
        ".config/Happ/subs.db",
        ".config/autostart/Happ.desktop",
    }
    for item in manifest["items"]:
        relative = item["home_relative"]
        if relative not in allowed and not relative.startswith(".local/share/Happ/routing/"):
            raise ValueError("Unexpected Happ restore path")
    if database.is_file():
        backupHapp()
    else:
        current = [Path.home() / item["home_relative"] for item in manifest["items"]]
        newBackup([path for path in current if path.is_file() and path != database])
    actions: list[tuple[str, str]] = []
    for item in manifest["items"]:
        target = Path.home() / item["home_relative"]
        source = folder / item["backup"]
        content = source.read_bytes()
        if item.get("kind") == "sqliteSnapshot":
            connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
            try:
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("Happ snapshot database failed integrity check")
            finally:
                connection.close()
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = atomicWrite(target, content, 0o600, preserveExistingMode=False)
        actions.append((str(target), "restored" if result == "changed" else "unchanged"))
    return actions
