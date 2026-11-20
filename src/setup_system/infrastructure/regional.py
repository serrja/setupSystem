"""Read and apply Debian's native timezone, keyboard and locale settings."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path

from setup_system import installation as system
from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.infrastructure.trusted_bootstrap import protected
from setup_system.system_inspection import InspectionPaths, _timezone_file, validate_timezone

KEYBOARD = Path("/etc/default/keyboard")
LOCALE = Path("/etc/locale.conf")
STATE = Path("/var/lib/setup-system/regional")
LOCALE_GEN = Path("/etc/locale.gen")
SUPPORTED = Path("/usr/share/i18n/SUPPORTED")


def prerequisites(setting: str) -> tuple[str, ...]:
    commands = {
        "locale": (Path("/usr/sbin/update-locale"), "locales"),
        "keyboard": (Path("/usr/bin/setupcon"), "console-setup"),
    }
    pair = commands.get(setting)
    return (pair[1],) if pair and not pair[0].is_file() else ()


def _generated(value: str) -> bool:
    return (
        value.lower().replace("utf-8", "utf8")
        in _command(["/usr/bin/locale", "-a"]).lower().splitlines()
    )


def _generate(value: str, journal: Path, record: dict[str, object]) -> None:
    if _generated(value):
        return
    entry = value + " UTF-8"
    if entry not in SUPPORTED.read_text().splitlines():
        raise ValueError(f"Locale {value} is unavailable in the installed Debian locale data")
    protected(LOCALE_GEN, directory=False)
    original = LOCALE_GEN.read_bytes() if LOCALE_GEN.is_file() else b""
    if entry not in original.decode().splitlines():
        backup = STATE / "backups" / (hashlib.sha256(original).hexdigest() + ".gen")
        system._write_system(backup, original)
        record["locale_gen_backup"] = str(backup)
        record["locale_gen_existed"] = LOCALE_GEN.exists()
        system._write_system(journal, json.dumps(record).encode())
        system._write_system(
            LOCALE_GEN,
            original.rstrip(b"\n") + b"\n" + entry.encode() + b"\n",
            stat.S_IMODE(LOCALE_GEN.stat().st_mode) if LOCALE_GEN.exists() else 0o644,
        )
    _command(["/usr/sbin/locale-gen", "--keep-existing"])
    if not _generated(value):
        raise RuntimeError("Locale generation readback failed")


def _value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError(f"Unexpected {path} configuration")
    values = re.findall(rf"(?m)^{key}=(.*)$", path.read_text())
    if len(values) > 1:
        raise ValueError(f"Ambiguous {key} in {path}")
    return values[0].strip().strip("\"'") if values else None


def equivalent(setting: str, observed: str | None, desired: str) -> bool:
    if observed is None:
        return False
    if setting == "timezone":
        zoneinfo = InspectionPaths().zoneinfo
        return (zoneinfo / observed).resolve() == (zoneinfo / desired).resolve()
    if setting == "locale":
        return observed.lower().replace("utf-8", "utf8") == desired.lower().replace("utf-8", "utf8")
    return observed == desired


def actual(setting: str) -> str | None:
    if setting == "timezone":
        paths = InspectionPaths()
        return _timezone_file(paths) if paths.localtime.is_file() else None
    if setting == "keyboard":
        return _value(KEYBOARD, "XKBLAYOUT")
    if setting == "locale":
        return _value(LOCALE, "LANG")
    raise ValueError("Unknown regional setting")


def _command(args: list[str]) -> str:
    return system.run_command(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C"},
    ).stdout


def validate(setting: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Select an explicit {setting}")
    if setting == "timezone" and validate_timezone(value) == value:
        return value
    if setting == "keyboard" and value in {"us", "us,ru"}:
        return value
    if setting == "locale" and re.fullmatch(r"(?:C|[a-z]{2,3}_[A-Z]{2})\.UTF-8", value):
        if (
            not _generated(value)
            and SUPPORTED.is_file()
            and value + " UTF-8" not in SUPPORTED.read_text().splitlines()
        ):
            raise ValueError(f"Locale {value} is not supported by Debian locale data")
        return value
    raise ValueError(f"Unsupported {setting} selection")


def apply(setting: str, value: object, expected: object) -> None:
    desired = validate(setting, value)
    before = actual(setting)
    if equivalent(setting, before, desired):
        return
    if before != expected:
        raise ValueError(f"System {setting} changed after plan confirmation")
    path = {"keyboard": KEYBOARD, "locale": LOCALE}.get(setting)
    record: dict[str, object] = {
        "schema_version": 1,
        "setting": setting,
        "before": before,
        "after": desired,
        "status": "running",
    }
    original = b""
    if path is not None:
        protected(path, directory=False)
        if path.exists():
            info = path.stat()
            if info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                raise ValueError("Unsupported regional configuration file")
            original = path.read_bytes()
            backup = STATE / "backups" / (hashlib.sha256(original).hexdigest() + ".conf")
            system._write_system(backup, original)
            record.update(backup=str(backup), mode=stat.S_IMODE(info.st_mode))
    journal = STATE / (setting + ".json")
    system._write_system(journal, json.dumps(record).encode())
    try:
        if setting == "timezone":
            _command(["/usr/bin/timedatectl", "set-timezone", desired])
        elif setting == "locale":
            _generate(desired, journal, record)
            _command(["/usr/sbin/update-locale", "LANG=" + desired])
        else:
            text = original.decode()
            matches = re.findall(r"(?m)^\s*XKBLAYOUT\s*=.*$", text)
            if len(matches) > 1 or (
                matches and not matches[0].lstrip("\n").startswith("XKBLAYOUT=")
            ):
                raise ValueError("Ambiguous keyboard layout assignment")
            replacement = f'XKBLAYOUT="{desired}"'
            if matches:
                text = re.sub(r"(?m)^XKBLAYOUT=.*$", replacement, text)
            else:
                text = text.rstrip("\n") + "\n" + replacement + "\n"
            mode = stat.S_IMODE(KEYBOARD.stat().st_mode) if KEYBOARD.exists() else 0o644
            system._write_system(KEYBOARD, text.encode(), mode)
            _command(["/usr/bin/setupcon", "--keyboard-only", "--save-only"])
        if not equivalent(setting, actual(setting), desired):
            raise RuntimeError(f"{setting.title()} readback failed")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        record["status"] = "partial"
        system._write_system(journal, json.dumps(record).encode())
        raise PartialApplyError(error_text(exc)) from exc
    record["status"] = "complete"
    system._write_system(journal, json.dumps(record).encode())
