"""Validate local IANA zone data and inspect time synchronization providers."""

from __future__ import annotations

import re
import stat
import struct
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

from setup_system.domain.system_facts import ClockSnapshot, InspectionPaths
from setup_system.infrastructure.inspection_commands import (
    TIME_PROVIDERS,
    InspectionRunner,
    read_query,
)

MAX_TZIF_BYTES = 1024 * 1024


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
            or target.stat().st_size > MAX_TZIF_BYTES
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


def timezone_file(paths: InspectionPaths) -> str | None:
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
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_TZIF_BYTES:
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


def inspect_clock(run: InspectionRunner, paths: InspectionPaths) -> ClockSnapshot:
    output = read_query(
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
    timezone = timezone_file(paths)
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
        timezone=timezone,
        ntp_enabled=boolean.get(values.get("NTP", "")),
        synchronized=boolean.get(values.get("NTPSynchronized", "")),
        rtc_local=boolean.get(values.get("LocalRTC", "")),
        can_ntp=boolean.get(values.get("CanNTP", "")),
        providers=tuple(providers),
        providers_known=known,
    )
