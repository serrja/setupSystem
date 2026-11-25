"""Observe native sudo policy and timestamp availability in the current context."""

import asyncio
import base64
import json
import os
import pwd
import re
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

import pexpect  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from setup_system.infrastructure.system_paths import protected


def interactive_logins() -> list[str]:
    shells = {line for line in Path("/etc/shells").read_text().splitlines() if line.startswith("/")}
    logins = []
    for line in Path("/etc/passwd").read_text().splitlines():
        name, _, uid, _, _, home, shell = line.split(":")
        if (
            int(uid) >= 1000
            and int(uid) != 65534
            and home.startswith("/")
            and shell in shells
            and not shell.endswith(("nologin", "false"))
        ):
            logins.append(name)
    return logins


class BecomePassword:
    """Transfer a single response to Ansible's hidden prompt."""

    def __init__(self, value: str) -> None:
        if (
            not value
            or len(value.encode()) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("Enter a non-empty single-line password")
        self._response = value.encode()

    def take(self) -> bytes:
        response, self._response = self._response, b""
        if not response:
            raise RuntimeError("The authorization response has expired")
        return response

    def discard(self) -> None:
        self._response = b""


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


_PLUGIN_NAME = re.compile(r"SETUP_PLUGIN_[A-Z0-9_]{1,108}")


def encoded_preferences() -> str:
    values = {k: v for k, v in os.environ.items() if _PLUGIN_NAME.fullmatch(k)}
    raw = json.dumps(values).encode()
    if len(raw) > 65536:
        raise ValueError("Plugin environment overrides exceed the launch limit")
    return base64.b64encode(raw).decode("ascii")


def restore_preferences() -> None:
    data = os.environ.pop("SETUP_SYSTEM_PLUGIN_OVERRIDES", None)
    if data is None:
        return
    if len(data) > 90000:
        raise ValueError("Plugin environment overrides exceed the launch limit")
    values = TypeAdapter(dict[str, str]).validate_json(
        base64.b64decode(data, validate=True), strict=True
    )
    if not all(_PLUGIN_NAME.fullmatch(k) and "\x00" not in v for k, v in values.items()):
        raise ValueError("Invalid plugin environment override")
    os.environ.update(values)


async def prepare_sudo(
    account: str, prompt: Callable[[], Awaitable[BecomePassword | None]], timeout: float
) -> str:
    if account != pwd.getpwuid(os.getuid()).pw_name:
        return "Open Administration to prepare another account with a confirmed root operation."
    sudo = Path("/usr/bin/sudo")
    await asyncio.to_thread(protected, sudo, directory=False)
    marker = b"setup-system authorization: "
    child = await asyncio.to_thread(
        pexpect.spawn,
        str(sudo),
        ["-p", marker.decode(), "-v"],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        timeout=timeout,
    )
    try:
        patterns = [
            re.compile(re.escape(marker) + rb"\Z"),
            pexpect.EOF,
            re.compile(rb"(?s).{8192}"),
        ]
        matched = await child.expect(patterns, async_=True)
        if matched == 0:
            if child.getecho():
                raise ValueError("Native password prompt has terminal echo enabled")
            token = await prompt()
            if token is None:
                return "Authorization cancelled."
            try:
                if child.getecho():
                    raise ValueError("Native terminal echo changed")
                child.sendline(token.take())
            finally:
                token.discard()
            matched = await child.expect(patterns, async_=True)
        if matched != 1:
            return "Native authorization was refused or requested another challenge."
        await asyncio.to_thread(child.close)
        if child.exitstatus != 0:
            return "Native authorization was refused."
        return "Sudo verified the selected login in this terminal. Apply rechecks its own context."
    except pexpect.ExceptionPexpect:
        return "The native authorization terminal was unavailable."
    finally:
        await asyncio.to_thread(child.close, force=True)
