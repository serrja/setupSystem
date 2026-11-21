"""Verified ordinary accounts and their HOME and XDG boundaries."""

from __future__ import annotations

import grp
import os
import pwd
import re
from collections.abc import Mapping
from pathlib import Path

from setup_system.domain.identity import TargetUser


def validate_user_path(path: Path, target: TargetUser) -> Path:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "." in str(path).split("/")
        or any(ord(c) < 32 for c in str(path))
        or not path.is_relative_to(target.home)
    ):
        raise ValueError("Managed paths must stay inside the verified HOME without traversal")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Managed paths must not traverse symlinks")
        if parent != path and parent.exists() and not parent.is_dir():
            raise ValueError("Managed parent is not a directory")
        if parent.exists() and parent != target.home and parent.is_relative_to(target.home):
            if parent.stat().st_uid != target.uid:
                raise ValueError("Managed user path has a different owner")
    return path


def validate_home(target: TargetUser) -> None:
    if target.home in {Path("/"), Path("/root")} or not target.home.is_dir():
        raise ValueError("Target HOME must exist and must not be root HOME")
    validate_user_path(target.home, target)
    if target.home.stat().st_uid != target.uid:
        raise ValueError("Target HOME must be owned by the target user")
    if Path(target.shell).name in {"false", "nologin"}:
        raise ValueError("Target account must support an interactive login")


def resolve_target(
    name: str | None,
    *,
    environment: Mapping[str, str] | None = None,
    effective_uid: int | None = None,
    real_uid: int | None = None,
) -> TargetUser:
    env = os.environ if environment is None else environment
    euid = os.geteuid() if effective_uid is None else effective_uid
    uid = os.getuid() if real_uid is None else real_uid
    sudo = env.get("SUDO_USER")
    if sudo and sudo != "root":
        if not env.get("SUDO_UID") or not env.get("SUDO_GID") or (name and name != sudo):
            raise ValueError("Incomplete or conflicting sudo identity")
        name = sudo
    elif euid == 0 and not name:
        raise ValueError("Direct root invocation requires --user NAME")
    elif not name:
        name = pwd.getpwuid(uid).pw_name
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*\$?", name):
        raise ValueError("Invalid target user")
    try:
        record = pwd.getpwnam(name)
        grp.getgrgid(record.pw_gid)
    except KeyError as exc:
        raise ValueError("Unknown target account or primary group") from exc
    if record.pw_name != name or record.pw_uid == 0 or record.pw_gid < 0:
        raise ValueError("Root/ambiguous accounts cannot be the desktop target")
    if euid != 0 and record.pw_uid != uid:
        raise ValueError("An unprivileged invocation can configure only the current user")
    if (
        sudo
        and sudo != "root"
        and (env["SUDO_UID"] != str(record.pw_uid) or env["SUDO_GID"] != str(record.pw_gid))
    ):
        raise ValueError("SUDO_UID/SUDO_GID do not match the passwd account")
    target = TargetUser(name, record.pw_uid, record.pw_gid, Path(record.pw_dir), record.pw_shell)
    validate_home(target)
    return target


def user_environment(target: TargetUser, overrides: Mapping[str, Path]) -> dict[str, str]:
    defaults = {
        "config": ".config",
        "data": ".local/share",
        "state": ".local/state",
        "cache": ".cache",
    }
    if set(overrides) - defaults.keys():
        raise ValueError("Unknown XDG override")
    env = {
        "HOME": str(target.home),
        "USER": target.name,
        "LOGNAME": target.name,
        "SHELL": target.shell,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key, suffix in defaults.items():
        path = validate_user_path(overrides.get(key, target.home / suffix), target)
        if path == target.home:
            raise ValueError("XDG roots must be subdirectories of HOME")
        env[f"XDG_{key.upper()}_HOME"] = str(path)
    return env
