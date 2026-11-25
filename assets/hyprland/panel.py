"""One session-owned bridge between Debian Waybar and Hyprland's Lua IPC."""

from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pydantic import JsonValue

WORKSPACES = tuple(range(1, 11))
UPDATE_SIGNAL = 8
MAX_REPLY = 1024 * 1024
EVENTS = frozenset(
    {
        "workspace",
        "workspacev2",
        "focusedmon",
        "createworkspace",
        "destroyworkspace",
        "openwindow",
        "closewindow",
        "movewindow",
        "movewindowv2",
        "urgent",
    }
)


def _text(path: Path) -> str:
    try:
        with path.open() as stream:
            return stream.read(128).strip()
    except OSError:
        return ""


def _number(path: Path) -> int | None:
    value = _text(path)
    return int(value) if re.fullmatch(r"[0-9]{1,10}", value) else None


def hardware(sysfs: Path = Path("/sys")) -> tuple[tuple[str, ...], str | None]:
    """Select present system batteries and the same native/raw backlight as the keys."""
    batteries = tuple(
        path.name
        for path in sorted((sysfs / "class/power_supply").glob("*"))
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", path.name)
        and _text(path / "type") == "Battery"
        and _text(path / "scope") != "Device"
        and _text(path / "present") != "0"
    )
    backlights = [
        path
        for path in sorted((sysfs / "class/backlight").glob("*"))
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", path.name)
        and (_number(path / "max_brightness") or 0) > 0
        and _number(path / "brightness") is not None
    ]
    selected = next((path for path in backlights if _text(path / "type") == "raw"), None)
    return batteries, (selected or backlights[0]).name if backlights else None


def configuration(
    template: dict[str, JsonValue], state: Path, sysfs: Path = Path("/sys")
) -> dict[str, JsonValue]:
    """Use Waybar's stable custom-module protocol instead of legacy dispatch strings."""
    result = deepcopy(template)
    result["modules-left"] = ["custom/launcher", *(f"custom/workspace-{n}" for n in WORKSPACES)]
    for number in WORKSPACES:
        cache = shlex.quote(str(state / f"workspace-{number}.json"))
        ready = shlex.quote(str(state / "ready"))
        result[f"custom/workspace-{number}"] = {
            "exec": f"cat -- {cache} && : > {ready}",
            "return-type": "json",
            "signal": UPDATE_SIGNAL,
            "exec-on-event": False,
            "on-click": f"hyprctl dispatch 'hl.dsp.focus({{ workspace = {number} }})'",
            "on-scroll-up": "hyprctl dispatch 'hl.dsp.focus({ workspace = \"e-1\" })'",
            "on-scroll-down": "hyprctl dispatch 'hl.dsp.focus({ workspace = \"e+1\" })'",
            "escape": True,
        }
    batteries, backlight = hardware(sysfs)
    modules = result["modules-right"]
    if not isinstance(modules, list) or any(not isinstance(name, str) for name in modules):
        raise ValueError("Expected a Waybar module list")
    right: list[JsonValue] = [name for name in modules if name not in ("battery", "backlight")]
    for index, name in enumerate(batteries):
        module = f"battery#system-{index}"
        result[module] = {
            "bat": name,
            "interval": 10,
            "format": "Bat {capacity}%",
            "format-charging": "Charging {capacity}%",
            "format-full": "Full {capacity}%",
            "states": {"warning": 30, "critical": 15},
            "tooltip-format": name + ": {capacity}% · {time}",
        }
        right.insert(max(0, len(right) - 2), module)
    if backlight is not None:
        control = result["backlight"]
        if not isinstance(control, dict):
            raise ValueError("Expected Waybar backlight options")
        control["device"] = backlight
        right.insert(max(0, len(right) - 2), "backlight")
    result["modules-right"] = right
    return result


PanelRow = TypedDict("PanelRow", {"text": str, "class": list[str], "tooltip": str})


def _query(topic: str) -> dict[str, JsonValue] | list[JsonValue]:
    # This session asset can outlive the configurator package.
    with (
        subprocess.Popen(
            ["hyprctl", "-j", topic],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process,
        selectors.DefaultSelector() as reader,
    ):
        assert process.stdout is not None
        reader.register(process.stdout, selectors.EVENT_READ)
        raw = bytearray()
        deadline = time.monotonic() + 2
        try:
            while len(raw) <= MAX_REPLY:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not reader.select(remaining):
                    raise TimeoutError("Hyprland IPC response timed out")
                block = os.read(process.stdout.fileno(), min(65536, MAX_REPLY + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
            if len(raw) > MAX_REPLY:
                raise ValueError("Hyprland IPC response exceeds its bound")
            if process.wait(timeout=max(0, deadline - time.monotonic())):
                raise ValueError("Hyprland IPC request failed")
        finally:
            if process.poll() is None:
                process.kill()
    data = json.loads(raw)
    if isinstance(data, (list, dict)):
        return data
    raise ValueError("Unexpected workspace response")


def workspace_state() -> dict[int, PanelRow]:
    """Read only current compositor state; unavailable state is never presented as active."""
    try:
        current, entries = _query("activeworkspace"), _query("workspaces")
        if not isinstance(current, dict) or not isinstance(entries, list):
            raise ValueError("Unexpected workspace response")
        active = current.get("id")
        if type(active) is not int:
            raise ValueError("Invalid active workspace")
        occupied = {
            workspace
            for row in entries
            if isinstance(row, dict)
            and type(workspace := row.get("id")) is int
            and type(windows := row.get("windows")) is int
            and windows > 0
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        active, occupied = None, set()
    return {
        number: {
            "text": str(number),
            "class": [
                "workspace",
                "active" if number == active else "inactive",
                "occupied" if number in occupied else "empty",
            ],
            "tooltip": f"Workspace {number}" + (" · status unavailable" if active is None else ""),
        }
        for number in WORKSPACES
    }


def publish(folder: Path, rows: dict[int, PanelRow]) -> None:
    for number, row in rows.items():
        path = folder / f"workspace-{number}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(row) + "\n")
        temporary.replace(path)


def _runtime() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if (
        os.geteuid() == 0
        or not runtime.is_absolute()
        or runtime.is_symlink()
        or not runtime.is_dir()
        or runtime.stat().st_uid != os.getuid()
        or runtime.stat().st_mode & 0o077
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", signature) is None
    ):
        raise ValueError("Panel needs the real user's private Hyprland runtime directory")
    return runtime


def run(profile: Path) -> int:
    runtime = _runtime()
    endpoint = runtime / "hypr" / os.environ["HYPRLAND_INSTANCE_SIGNATURE"] / ".socket2.sock"
    stopped = Event()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: stopped.set())
    with tempfile.TemporaryDirectory(prefix="setup-system-panel-", dir=runtime) as temporary:
        folder = Path(temporary)
        rows = workspace_state()
        publish(folder, rows)
        config = configuration(json.loads((profile / "waybar.json").read_text()), folder)
        config_path = folder / "waybar.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        process = subprocess.Popen(
            ["waybar", "-c", str(config_path), "-s", str(profile / "style.css")]
        )
        connection: socket.socket | None = None
        selector = selectors.DefaultSelector()
        pending = b""
        refresh_needed = True
        try:
            while process.poll() is None and not stopped.is_set():
                if refresh_needed and (folder / "ready").is_file():
                    process.send_signal(signal.SIGRTMIN + UPDATE_SIGNAL)
                    refresh_needed = False
                if connection is None:
                    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        connection.settimeout(1)
                        connection.connect(str(endpoint))
                        connection.setblocking(False)
                        selector.register(connection, selectors.EVENT_READ)
                        updated = workspace_state()
                        if updated != rows:
                            rows = updated
                            publish(folder, rows)
                            refresh_needed = True
                    except OSError:
                        connection.close()
                        connection = None
                        stopped.wait(1)
                        continue
                if not selector.select(timeout=1):
                    continue
                try:
                    block = connection.recv(65536)
                except BlockingIOError:
                    continue
                if not block:
                    break
                pending += block
                if len(pending) > MAX_REPLY:
                    raise ValueError("Hyprland event exceeds its bound")
                lines = pending.split(b"\n")
                pending = lines.pop()
                if not any(
                    line.split(b">>", 1)[0].decode("ascii", "ignore") in EVENTS for line in lines
                ):
                    continue
                updated = workspace_state()
                if updated != rows:
                    rows = updated
                    publish(folder, rows)
                    refresh_needed = True
            return process.returncode or 0
        finally:
            selector.close()
            if connection is not None:
                connection.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: panel.py PROFILE_DIRECTORY")
        raise SystemExit(run(Path(sys.argv[1])))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Panel stopped: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(4) from None
