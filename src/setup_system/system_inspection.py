"""Read-only encryption and clock observations without changing login or disk state."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import struct
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

COMMAND_TIMEOUT = 10
MAX_OUTPUT = 1024 * 1024
FS_ENCRYPT_FL = 0x00000800
TIME_PROVIDERS = ("chrony", "ntpsec", "openntpd", "systemd-timesyncd")
InspectionRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
DirectoryFlags = Callable[[Path], int | None]


class EncryptionState(StrEnum):
    ENCRYPTED = "encrypted"
    LOCKED = "locked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EncryptionInfo:
    state: EncryptionState
    layers: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class EncryptionSnapshot:
    root: EncryptionInfo
    home: EncryptionInfo


@dataclass(frozen=True)
class ClockSnapshot:
    """Current clock properties and installed (not necessarily active) time providers."""

    timezone: str | None
    ntp_enabled: bool | None = None
    synchronized: bool | None = None
    rtc_local: bool | None = None
    can_ntp: bool | None = None
    providers: tuple[str, ...] = ()
    providers_known: bool = False


@dataclass(frozen=True)
class SystemSnapshot:
    encryption: EncryptionSnapshot
    clock: ClockSnapshot


@dataclass(frozen=True)
class InspectionPaths:
    zoneinfo: Path = Path("/usr/share/zoneinfo")
    timezone: Path = Path("/etc/timezone")
    localtime: Path = Path("/etc/localtime")


@dataclass(frozen=True)
class _Mount:
    source: str
    filesystem: str


def _safe_absolute(value: str) -> bool:
    return (
        Path(value).is_absolute()
        and ".." not in Path(value).parts
        and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)
    )


def _query_allowed(argv: list[str]) -> bool:
    if argv == [
        "/usr/bin/timedatectl",
        "show",
        "--all",
        "--property=Timezone,NTP,NTPSynchronized,LocalRTC,CanNTP",
    ]:
        return True
    if len(argv) == 6 and argv[:3] == ["/usr/bin/findmnt", "--json", "--target"]:
        return _safe_absolute(argv[3]) and argv[4:] == ["--output", "SOURCE,FSTYPE"]
    if len(argv) == 8 and argv[:7] == [
        "/usr/bin/lsblk",
        "--json",
        "--inverse",
        "--tree",
        "--output",
        "NAME,TYPE,FSTYPE",
        "--",
    ]:
        return (
            _safe_absolute(argv[7])
            and re.fullmatch(r"/dev/[A-Za-z0-9_+./:-]+", argv[7]) is not None
        )
    if len(argv) == 3 and argv[:2] == ["/usr/bin/fscrypt", "status"]:
        return _safe_absolute(argv[2])
    if len(argv) == 4 and argv[:3] == ["/usr/bin/lsattr", "-d", "--"]:
        return _safe_absolute(argv[3])
    return (
        len(argv) == 4
        and argv[:3] == ["/usr/bin/dpkg-query", "--show", "--showformat=${Status}"]
        and argv[3] in TIME_PROVIDERS
    )


def run_inspection(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run only fixed read-only queries with no inherited proxy or account environment."""
    if not _query_allowed(argv):
        raise ValueError("Expected a fixed read-only inspection query")
    return subprocess.run(
        argv,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
    )


def _command(run: InspectionRunner, argv: list[str], *, missing_key: bool = False) -> str | None:
    try:
        result = run(argv)
    except OSError as exc:
        if missing_key and exc.errno == errno.ENOKEY:
            raise
        return None
    except subprocess.SubprocessError:
        return None
    if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > MAX_OUTPUT:
        return None
    return result.stdout


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Invalid inspection object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("Invalid inspection key")
        result[key] = item
    return result


def _mount(path: Path, run: InspectionRunner) -> _Mount | None:
    output = _command(
        run,
        ["/usr/bin/findmnt", "--json", "--target", str(path), "--output", "SOURCE,FSTYPE"],
    )
    if output is None:
        return None
    try:
        filesystems = _object(json.loads(output)).get("filesystems")
        if not isinstance(filesystems, list) or len(filesystems) != 1:
            return None
        value = _object(filesystems[0])
        source, filesystem = value.get("source"), value.get("fstype")
        if not isinstance(source, str) or not isinstance(filesystem, str):
            return None
        return _Mount(source, filesystem.lower())
    except (ValueError, TypeError, RecursionError):
        return None


def _block_layers(source: str, run: InspectionRunner) -> tuple[str, ...] | None:
    # Btrfs findmnt sources may append a subvolume; only the device is queried.
    device = source.partition("[")[0]
    if (
        not re.fullmatch(r"/dev/[A-Za-z0-9_+./:-]+", device)
        or ".." in Path(device).parts
        or len(device) > 4096
    ):
        return None
    output = _command(
        run,
        [
            "/usr/bin/lsblk",
            "--json",
            "--inverse",
            "--tree",
            "--output",
            "NAME,TYPE,FSTYPE",
            "--",
            device,
        ],
    )
    if output is None:
        return None
    try:
        devices = _object(json.loads(output)).get("blockdevices")
        if not isinstance(devices, list) or not devices:
            return None
        pending = [(item, frozenset[str]()) for item in devices]
        layers: set[str] = set()
        count = 0
        while pending:
            item, ancestors = pending.pop()
            value = _object(item)
            count += 1
            name, kind, filesystem = value.get("name"), value.get("type"), value.get("fstype")
            if (
                count > 4096
                or len(ancestors) >= 64
                or not isinstance(name, str)
                or not name
                or name in ancestors
                or not isinstance(kind, str)
                or (filesystem is not None and not isinstance(filesystem, str))
            ):
                return None
            if kind == "crypt":
                layers.add("dm-crypt")
            if isinstance(filesystem, str) and filesystem.lower() == "crypto_luks":
                layers.add("LUKS")
            children = value.get("children", [])
            if not isinstance(children, list):
                return None
            pending.extend((child, ancestors | {name}) for child in children)
        return tuple(sorted(layers))
    except (ValueError, TypeError, RecursionError):
        return None


def _encryption(path: Path, run: InspectionRunner) -> EncryptionInfo:
    return _mount_encryption(_mount(path, run), run)


def _mount_encryption(mount: _Mount | None, run: InspectionRunner) -> EncryptionInfo:
    if mount is None:
        return EncryptionInfo(EncryptionState.UNKNOWN, (), "Mount information is unavailable")
    if mount.filesystem == "ecryptfs":
        return EncryptionInfo(EncryptionState.ENCRYPTED, ("eCryptfs",), "Existing eCryptfs mount")
    layers = _block_layers(mount.source, run)
    if layers:
        return EncryptionInfo(
            EncryptionState.ENCRYPTED, layers, "Existing encrypted block ancestry"
        )
    return EncryptionInfo(
        EncryptionState.UNKNOWN,
        (),
        "No conclusive encryption evidence; filesystem-level protection may still exist",
    )


def filesystem_flags(path: Path, *, run: InspectionRunner = run_inspection) -> int | None:
    """Use the filesystem's existing userspace attribute reader; never request a key."""
    output = _command(run, ["/usr/bin/lsattr", "-d", "--", str(path)], missing_key=True)
    if output is None:
        return None
    flags = output.split(maxsplit=1)
    if not flags or re.fullmatch(r"[A-Za-z-]{1,64}", flags[0]) is None:
        return None
    return FS_ENCRYPT_FL if "E" in flags[0] else 0


def _fscrypt_unlocked(home: Path, run: InspectionRunner) -> bool | None:
    """Read only the selected directory's explicit key-presence result, never a key."""
    output = _command(run, ["/usr/bin/fscrypt", "status", str(home)], missing_key=True)
    if output is None:
        return None
    states = re.findall(r"^Unlocked:[ \t]*(Yes|No)[ \t]*$", output, re.MULTILINE)
    return states[0] == "Yes" if len(states) == 1 else None


def _home_encryption(
    home: Path, run: InspectionRunner, home_flags: DirectoryFlags
) -> EncryptionInfo:
    try:
        details = home.lstat()
        if not stat.S_ISDIR(details.st_mode):
            return EncryptionInfo(EncryptionState.UNKNOWN, (), "HOME is not a regular directory")
        try:
            (home / ".ecryptfs").lstat()
        except FileNotFoundError:
            ecryptfs_marker = False
        else:
            ecryptfs_marker = True
    except OSError as exc:
        if exc.errno == errno.ENOKEY:
            return EncryptionInfo(
                EncryptionState.LOCKED,
                ("filesystem encryption",),
                "HOME requires its existing key unlock",
            )
        return EncryptionInfo(EncryptionState.UNKNOWN, (), "HOME protection could not be inspected")
    mount = _mount(home, run)
    if ecryptfs_marker and (mount is None or mount.filesystem != "ecryptfs"):
        return EncryptionInfo(
            EncryptionState.LOCKED if mount is not None else EncryptionState.UNKNOWN,
            ("eCryptfs",),
            "Existing eCryptfs HOME is not verified as mounted; unlock it through "
            "the existing local login before setup",
        )
    result = _mount_encryption(mount, run)
    if mount is not None and mount.filesystem == "ecryptfs":
        return result
    try:
        flags = home_flags(home)
    except OSError as exc:
        if exc.errno == errno.ENOKEY:
            return EncryptionInfo(
                EncryptionState.LOCKED,
                ("filesystem encryption",),
                "HOME requires its existing key unlock",
            )
        return EncryptionInfo(EncryptionState.UNKNOWN, (), "HOME protection could not be inspected")
    if type(flags) is int and flags & FS_ENCRYPT_FL:
        layers = tuple(dict.fromkeys((*result.layers, "fscrypt")))
        try:
            unlocked = _fscrypt_unlocked(home, run)
        except OSError as exc:
            if exc.errno == errno.ENOKEY:
                unlocked = False
            else:
                unlocked = None
        if unlocked is True:
            return EncryptionInfo(
                EncryptionState.ENCRYPTED,
                layers,
                "Existing fscrypt HOME is unlocked; preserve its PAM unlock integration",
            )
        if unlocked is False:
            return EncryptionInfo(
                EncryptionState.LOCKED,
                layers,
                "Existing fscrypt HOME must be unlocked by normal login",
            )
        return EncryptionInfo(
            EncryptionState.UNKNOWN,
            layers,
            "Existing fscrypt policy; HOME unlock state is unavailable "
            "and must be verified before setup",
        )
    return result


def validate_home_access(
    home: Path,
    *,
    run: InspectionRunner = run_inspection,
    home_flags: DirectoryFlags | None = None,
) -> EncryptionInfo:
    """Recheck encryption and directory access immediately before a user worker writes."""
    if not _safe_absolute(str(home)):
        raise ValueError("HOME access requires an absolute path without traversal")
    try:
        descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ValueError(
            "HOME is locked or unreadable; use the existing local login first"
        ) from None
    try:
        opened = os.fstat(descriptor)
        flags = (lambda path: filesystem_flags(path, run=run)) if home_flags is None else home_flags
        protection = _home_encryption(home, run, flags)
        if protection.state == EncryptionState.LOCKED or (
            protection.state == EncryptionState.UNKNOWN
            and any(layer.casefold() in {"fscrypt", "ecryptfs"} for layer in protection.layers)
        ):
            raise ValueError(
                "The encrypted HOME is locked or its unlock status is unknown; "
                "use the existing local login first"
            )
        # Consume at most one entry, without returning names or creating a probe file.
        with os.scandir(descriptor) as entries:
            next(entries, None)
        current = home.lstat()
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ValueError("HOME changed during access verification; review a fresh plan")
        return protection
    except OSError:
        raise ValueError(
            "HOME is locked or unreadable; use the existing local login first"
        ) from None
    finally:
        os.close(descriptor)


def validate_timezone(
    value: str | None, *, zoneinfo_root: Path = Path("/usr/share/zoneinfo")
) -> str | None:
    """Validate a chosen IANA TZif entry; an empty choice preserves the system setting."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if (
        len(value) > 255
        or re.fullmatch(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*", value) is None
        or value.split("/", 1)[0] in {"posix", "right"}
    ):
        raise ValueError("Choose an installed IANA timezone such as Region/City")
    try:
        root = zoneinfo_root.resolve(strict=True)
        target = (root / value).resolve(strict=True)
        if (
            not target.is_relative_to(root)
            or not target.is_file()
            or target.stat().st_size > MAX_OUTPUT
        ):
            raise ValueError
        with target.open("rb") as source:
            if source.read(4) != b"TZif":
                raise ValueError
            source.seek(0)
            ZoneInfo.from_file(source, key=value)
    except (OSError, ValueError, EOFError, OverflowError, RuntimeError, struct.error):
        raise ValueError("Choose an installed IANA timezone such as Region/City") from None
    return value


def _timezone_file(paths: InspectionPaths) -> str | None:
    # /etc/localtime drives libc and Waybar; /etc/timezone can be a stale label.
    local_content: bytes | None = None
    try:
        root = paths.zoneinfo.resolve(strict=True)
        target = paths.localtime.resolve(strict=True)
        if target.is_relative_to(root):
            return validate_timezone(str(target.relative_to(root)), zoneinfo_root=root)
        if paths.localtime.is_symlink():
            return None
        details = paths.localtime.stat()
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_OUTPUT:
            return None
        local_content = paths.localtime.read_bytes()
    except FileNotFoundError:
        if paths.localtime.is_symlink():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        if (
            paths.timezone.is_file()
            and not paths.timezone.is_symlink()
            and paths.timezone.stat().st_size <= 256
        ):
            value = paths.timezone.read_text().strip()
            selected = validate_timezone(value, zoneinfo_root=paths.zoneinfo)
            if selected and (
                local_content is None or local_content == (paths.zoneinfo / selected).read_bytes()
            ):
                return selected
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def _clock(run: InspectionRunner, paths: InspectionPaths) -> ClockSnapshot:
    output = _command(
        run,
        [
            "/usr/bin/timedatectl",
            "show",
            "--all",
            "--property=Timezone,NTP,NTPSynchronized,LocalRTC,CanNTP",
        ],
    )
    values: dict[str, str] = {}
    if output is not None:
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in values:
                values.clear()
                break
            values[key] = value
    timezone = _timezone_file(paths)
    if values.get("Timezone"):
        try:
            timezone = validate_timezone(values["Timezone"], zoneinfo_root=paths.zoneinfo)
        except ValueError:
            pass
    boolean = {"yes": True, "no": False}
    providers: list[str] = []
    known = True
    for package in TIME_PROVIDERS:
        try:
            result = run(["/usr/bin/dpkg-query", "--show", "--showformat=${Status}", package])
        except (OSError, subprocess.SubprocessError):
            known = False
            continue
        if result.returncode == 0 and result.stdout == "install ok installed":
            providers.append(package)
        elif result.returncode != 1:
            known = False
    return ClockSnapshot(
        timezone,
        boolean.get(values.get("NTP", "")),
        boolean.get(values.get("NTPSynchronized", "")),
        boolean.get(values.get("LocalRTC", "")),
        boolean.get(values.get("CanNTP", "")),
        tuple(providers),
        known,
    )


def inspect_system(
    home: Path,
    *,
    run: InspectionRunner = run_inspection,
    paths: InspectionPaths | None = None,
    home_flags: DirectoryFlags | None = None,
) -> SystemSnapshot:
    """Observe current protection/clock state; UNKNOWN never means unencrypted."""
    if not _safe_absolute(str(home)):
        raise ValueError("System inspection requires an absolute HOME without traversal")
    paths = InspectionPaths() if paths is None else paths
    flags = (lambda path: filesystem_flags(path, run=run)) if home_flags is None else home_flags
    return SystemSnapshot(
        EncryptionSnapshot(_encryption(Path("/"), run), _home_encryption(home, run, flags)),
        _clock(run, paths),
    )
