"""Fixed stdlib sudo program: acquire a trusted release before importing root code.

This file is passed verbatim to the system interpreter, never loaded as a root module
from HOME. Only JSON data crosses stdin. No Python dependency is installed as root.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

HOSTS = frozenset({"github.com", "api.github.com", "release-assets.githubusercontent.com"})


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Unexpected privileged release origin")


def fetch(url: str, limit: int) -> bytes:
    validate_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HTTPSOnly())
    request = urllib.request.Request(url, headers={"User-Agent": "setupSystem"})
    with opener.open(request, timeout=60) as response:
        validate_url(response.url)
        data = cast(bytes, response.read(limit + 1))
    if len(data) > limit:
        raise ValueError("Release response exceeds its limit")
    return data


def caller() -> pwd.struct_passwd:
    if os.geteuid() != 0:
        raise ValueError("The privileged executor requires sudo")
    uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
    if not uid.isdecimal() or not gid.isdecimal() or int(uid) <= 0:
        raise ValueError("A verified non-root sudo caller is required")
    account = pwd.getpwuid(int(uid))
    if account.pw_gid != int(gid) or account.pw_name != os.environ.get("SUDO_USER"):
        raise ValueError("Sudo caller identity mismatch")
    home = Path(account.pw_dir)
    if (
        any(p.is_symlink() for p in (home, *home.parents))
        or not home.is_dir()
        or home.stat().st_uid != account.pw_uid
        or home.stat().st_mode & 0o022
    ):
        raise ValueError("Unsafe caller HOME")
    return account


def protected(path: Path, *, directory: bool = True) -> None:
    for item in reversed((path, *path.parents)):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                or (item != path and not stat.S_ISDIR(info.st_mode))
            ):
                raise ValueError("Privileged resources require root-owned non-writable paths")
    if path.exists() and directory and not path.is_dir():
        raise ValueError("Expected a protected directory")


def acquire(release: str, source_sha: str) -> Path:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?", release):
        raise ValueError("Invalid release identity")
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("Invalid source identity")
    base = "https://github.com/serrja/setupSystem/releases/download/" + release
    native = release[1:].replace("-dev.", ".dev")
    name = f"setup_system-{native}.tar.gz"
    receipt = json.loads(fetch(base + "/python-build-receipt.json", 262144))
    if receipt.get("sourceSha") != source_sha or receipt.get("schemaVersion") != 1:
        raise ValueError("Published native build provenance does not match the plan")
    hashes = fetch(base + "/checksums.sha256", 32768).decode().splitlines()
    candidates = [
        line.split()[0] for line in hashes if len(line.split()) == 2 and line.split()[1] == name
    ]
    if len(candidates) != 1 or not re.fullmatch(r"[0-9a-f]{64}", candidates[0]):
        raise ValueError("Missing unique release checksum")
    digest = candidates[0]
    files = receipt.get("files", [])
    if not any(item.get("name") == name and item.get("sha256") == digest for item in files):
        raise ValueError("Archive is missing from native build provenance")
    raw = fetch(base + "/" + name, 32 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Privileged source checksum mismatch")
    parent = Path("/var/lib/setup-system/releases")
    protected(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Always extract fresh verified bytes, never trust a writable cache or an old local tree.
    with tempfile.TemporaryDirectory(prefix=".verified-", dir=parent) as temporary:
        stage = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            names = [str(Path(item.name)).casefold() for item in members]
            if (
                len(members) > 4096
                or sum(item.size for item in members) > 32 * 1024 * 1024
                or len(names) != len(set(names))
                or any(
                    not (item.isfile() or item.isdir())
                    or item.size < 0
                    or Path(item.name).is_absolute()
                    or ".." in Path(item.name).parts
                    or "\\" in item.name
                    or any(part in {"", ".", ".."} for part in item.name.rstrip("/").split("/"))
                    or any(ord(char) < 32 or ord(char) == 127 for char in item.name)
                    or Path(item.name).parts[0] != f"setup_system-{native}"
                    for item in members
                )
            ):
                raise ValueError("Unsafe privileged source archive")
            archive.extractall(stage, filter="data")
        source = stage / f"setup_system-{native}"
        for item in source.rglob("*"):
            item.chmod(0o700 if item.is_dir() else 0o600)
        destination = parent / (source_sha + "-" + digest)
        protected(destination)
        # A prior complete copy is retained for recovery; use the fresh verified copy now.
        if destination.exists():
            destination = parent / (source_sha + "-" + stage.name.removeprefix(".verified-"))
        source.rename(destination)
    return destination


def validate_settings(action: str, settings: object, home: Path) -> None:
    fields = {
        "base": {
            "schema_version",
            "hyprland",
            "graphical_login",
            "timezone",
            "keyboard",
            "plymouth",
            "xdg",
            "expected_system_sha256",
        },
        "workspace_packages": {"packages", "happ"},
        "cleanup": {"packages", "mode", "closure", "xdg"},
    }
    if not isinstance(settings, dict) or not set(settings) <= fields[action]:
        raise ValueError("Unexpected privileged settings")
    if "xdg" in settings:
        xdg = settings["xdg"]
        if not isinstance(xdg, dict) or set(xdg) != {"config", "data", "state", "cache"}:
            raise ValueError("Invalid XDG resource kinds")
        for value in xdg.values():
            if not isinstance(value, str):
                raise ValueError("Invalid XDG path")
            path = Path(value)
            if (
                not path.is_absolute()
                or not path.is_relative_to(home)
                or path == home
                or ".." in path.parts
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("XDG paths must stay within caller HOME")
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ValueError("XDG paths cannot traverse symlinks")
    if action == "base":
        if type(settings.get("schema_version")) is not int or settings["schema_version"] != 1:
            raise ValueError("Unsupported system settings schema")
        for name in ("hyprland", "graphical_login", "plymouth"):
            if type(settings.get(name)) is not bool:
                raise ValueError("Invalid system feature selection")
        if settings.get("keyboard") not in {"us", "us,ru"}:
            raise ValueError("Unsupported keyboard layout")
        expected = settings.get("expected_system_sha256")
        if expected is not None and (
            not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)
        ):
            raise ValueError("Invalid system settings revision")
    else:
        packages = settings.get("packages")
        if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
            raise ValueError("Invalid package selection")
        if action == "workspace_packages" and type(settings.get("happ", False)) is not bool:
            raise ValueError("Invalid optional Happ selection")
        if action == "cleanup" and settings.get("mode") != "remove":
            raise ValueError("Purge and autoremove require separate reviewed operations")


@contextmanager
def operation_lock(directory: Path = Path("/run/setup-system")) -> Iterator[None]:
    protected(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(
        directory / "apply.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise ValueError("Unsafe privileged operation lock")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another system operation is running") from None
        yield


def main() -> int:
    account = caller()
    release = platform.freedesktop_os_release()
    if (
        release.get("ID") != "debian"
        or release.get("VERSION_ID") != "13"
        or platform.machine() not in {"x86_64", "aarch64", "arm64"}
    ):
        raise ValueError("Privileged workstation apply requires Debian 13 amd64/arm64")
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("Oversized privileged request")
    request = json.loads(raw)
    if not isinstance(request, dict) or set(request) != {
        "release",
        "source_sha",
        "action",
        "settings",
    }:
        raise ValueError("Invalid privileged request fields")
    if request["action"] not in {"base", "workspace_packages", "cleanup"}:
        raise ValueError("Operation is not allowlisted")
    validate_settings(request["action"], request["settings"], Path(account.pw_dir))
    if (
        not isinstance(request["release"], str)
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?", request["release"])
        or not isinstance(request["source_sha"], str)
        or not re.fullmatch(r"[a-f0-9]{40}", request["source_sha"])
    ):
        raise ValueError("Invalid release/source identity")
    with operation_lock():
        source = acquire(request["release"], request["source_sha"])
        # Only independently downloaded, root-owned, stdlib-only modules are imported here.
        sys.path.insert(0, str(source / "src"))
        from setup_system.infrastructure.privileged_tasks import execute

        return execute(source, account.pw_name, request["action"], request["settings"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.SubprocessError:
        print("System command failed; inspect /var/lib/setup-system/install.json", file=sys.stderr)
        raise SystemExit(5) from None
    except (OSError, ValueError, RuntimeError) as exc:
        # Provider errors never include response bodies, URLs or credentials.
        print("Privileged apply failed: " + str(exc), file=sys.stderr)
        raise SystemExit(5) from None
