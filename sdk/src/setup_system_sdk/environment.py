"""Forward only declared user session paths and verified sockets."""

import os
import pwd
import stat
from pathlib import Path


def _environment() -> dict[str, str]:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
    for key in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "CODEX_HOME",
        "WAYLAND_DISPLAY",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "DISPLAY",
    ):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def session_environment() -> dict[str, str]:
    """Forward only local session sockets owned by the caller to user processes."""
    env = _environment()
    uid = os.getuid()
    runtime = Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    bus = runtime / "bus"
    address = "unix:path=" + str(bus)
    try:
        folder = runtime.lstat()
        if (
            runtime.is_absolute()
            and runtime.resolve() == runtime
            and folder.st_uid == uid
            and stat.S_ISDIR(folder.st_mode)
            and not folder.st_mode & 0o077
            and os.environ.get("DBUS_SESSION_BUS_ADDRESS", address) == address
            and _user_socket(bus, uid)
        ):
            env["DBUS_SESSION_BUS_ADDRESS"] = address
    except OSError:
        pass
    return env


def _user_socket(path: Path, uid: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return path.is_absolute() and stat.S_ISSOCK(info.st_mode) and info.st_uid == uid


def ssh_socket() -> dict[str, str]:
    name = os.environ.get("SSH_AUTH_SOCK")
    return {"SSH_AUTH_SOCK": name} if name and _user_socket(Path(name), os.getuid()) else {}
