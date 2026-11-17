#!/usr/bin/python3 -IB
"""Select one registered user session after greetd/PAM authentication."""

from __future__ import annotations

import json
import os
import pwd
import stat
import sys
from pathlib import Path

REGISTRY = Path("@LOGIN_REGISTRY@")
XDG_KEYS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
SESSION_KEYS = (
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_ID",
    "XDG_SEAT",
    "XDG_VTNR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


def trusted(path: Path, *, owner: int = 0, boundary: Path = Path("/")) -> os.stat_result:
    if not path.is_absolute() or not path.is_relative_to(boundary):
        raise ValueError("Login registry escapes its trusted boundary")
    for parent in (path, *path.parents):
        if not parent.is_relative_to(boundary):
            break
        details = parent.lstat()
        if stat.S_ISLNK(details.st_mode) or details.st_uid != owner or details.st_mode & 0o022:
            raise ValueError("Login registry must be administrator controlled")
        if parent != path and not stat.S_ISDIR(details.st_mode):
            raise ValueError("Login registry parent is not a directory")
    result = path.stat()
    if not stat.S_ISREG(result.st_mode) or result.st_nlink != 1 or result.st_size > 16384:
        raise ValueError("Login registry has an unsafe file identity")
    return result


def session(
    registry: Path = REGISTRY, *, owner: int = 0, boundary: Path = Path("/")
) -> tuple[list[str], dict[str, str]]:
    uid = os.getuid()
    if (
        uid == 0
        or os.geteuid() != uid
        or os.environ.get("SSH_CONNECTION")
        or os.environ.get("SSH_TTY")
    ):
        raise ValueError("A local authenticated non-root desktop account is required")
    account = pwd.getpwuid(uid)
    if os.getgid() != account.pw_gid or os.getegid() != account.pw_gid:
        raise ValueError("Authenticated primary group does not match the account")
    path = registry / f"{uid}.json"
    trusted(path, owner=owner, boundary=boundary)
    record = json.loads(path.read_text())
    if (
        not isinstance(record, dict)
        or record.get("schema") != 1
        or any(
            record.get(key) != value
            for key, value in (
                ("uid", uid),
                ("gid", account.pw_gid),
                ("name", account.pw_name),
                ("home", account.pw_dir),
            )
        )
    ):
        raise ValueError("Login registry does not match the authenticated account")
    home = Path(account.pw_dir)
    overrides = record.get("environment")
    if not isinstance(overrides, dict) or set(overrides) != set(XDG_KEYS):
        raise ValueError("Invalid login XDG registry")
    environment = {
        "HOME": str(home),
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "SHELL": account.pw_shell,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    for key in XDG_KEYS:
        value = overrides[key]
        if not isinstance(value, str):
            raise ValueError("Invalid login XDG path")
        candidate = Path(value)
        if (
            not candidate.is_absolute()
            or ".." in candidate.parts
            or candidate == home
            or not candidate.is_relative_to(home)
        ):
            raise ValueError("Login XDG path escapes the account HOME")
        environment[key] = value
    launcher = Path(environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
    for parent in (launcher, *launcher.parents):
        if not parent.is_relative_to(home):
            break
        details = parent.lstat()
        if stat.S_ISLNK(details.st_mode) or details.st_uid != uid or details.st_mode & 0o022:
            raise ValueError("User session path has an unsafe type or owner")
    details = launcher.stat()
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise ValueError("User session launcher is not a regular single-link file")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    if (
        not runtime.is_absolute()
        or ".." in runtime.parts
        or any(ord(c) < 32 for c in str(runtime))
        or not runtime.is_dir()
        or runtime.is_symlink()
        or runtime.stat().st_uid != uid
        or stat.S_IMODE(runtime.stat().st_mode) != 0o700
    ):
        raise ValueError("PAM did not provide the user's private absolute runtime directory")
    for key in SESSION_KEYS:
        value = os.environ.get(key)
        if value is not None and not any(ord(c) < 32 for c in value):
            environment[key] = value
    provided_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    bus = runtime / "bus"
    if provided_bus == f"unix:path={bus}":
        try:
            bus_details = bus.lstat()
        except OSError:
            pass
        else:
            if stat.S_ISSOCK(bus_details.st_mode) and bus_details.st_uid == uid:
                environment["DBUS_SESSION_BUS_ADDRESS"] = provided_bus
    environment.update(
        XDG_CURRENT_DESKTOP="Hyprland", XDG_SESSION_TYPE="wayland", XDG_SESSION_CLASS="user"
    )
    return ["/bin/sh", str(launcher), "--display-manager"], environment


def main() -> int:
    try:
        arguments, environment = session()
        os.execve(arguments[0], arguments, environment)
    except (KeyError, OSError, ValueError):
        print(
            "The authenticated desktop session is unavailable; "
            "use a local TTY to review setupSystem.",
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
