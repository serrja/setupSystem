"""Remember a successful editor smoke for the exact profile, plugins and parser binaries."""

import hashlib
import json
import os
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import TypedDict

import typer
from pydantic import ConfigDict, TypeAdapter, with_config

from setup_system.domain.contracts import MAX_MESSAGE_BYTES
from setup_system.infrastructure.tool_paths import personal_path
from setup_system.infrastructure.user_storage import (
    atomic_write,
    checked_directory,
    safe_file,
    state_root,
)


@with_config(ConfigDict(extra="forbid", strict=True))
class EditorInputs(TypedDict):
    profiles: list[str]
    parsers: list[str]
    versions: dict[str, str]
    files: dict[str, list[str | int]]


def proof(request: EditorInputs) -> str | None:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    parsed = {}
    for name in request["parsers"]:
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError("Invalid editor parser name")
        file = data / "setup-system/editor-parsers/parser" / (name + ".so")
        personal_path(Path.home(), file, regular=True, max_bytes=None)
        if not file.is_file():
            return None
        parsed[name] = hashlib.sha256(file.read_bytes()).hexdigest()
    system = {}
    commands = ["nvim", "wl-copy", "wl-paste", "git", "fzf", "rg"]
    if "cpp" in request["profiles"]:
        commands += ["clangd", "clang-format"]
    for name in commands:
        path = Path("/usr/bin") / name
        if not os.access(path, os.X_OK):
            return None
        info = path.stat()
        system[name] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
    smoke = Path(__file__).with_name("editor_check.lua").read_bytes()
    values = [
        1,
        hashlib.sha256(smoke).hexdigest(),
        request["profiles"],
        request["versions"],
        request["files"],
        parsed,
        system,
    ]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def run(action: str, request: EditorInputs) -> bool:
    if os.geteuid() == 0 or action not in {"check", "record"}:
        raise ValueError("The editor receipt requires a normal user and a finite action")
    path = checked_directory(state_root()) / "editor-verification.v1"
    safe_file(path, os.getuid())
    if path.exists() and path.stat().st_size != 68:
        raise ValueError("Unrecognized editor verification receipt")
    previous = path.read_text() if path.is_file() else ""
    if path.exists() and not re.fullmatch(r"v1 [a-f0-9]{64}\n", previous):
        raise ValueError("Unrecognized editor verification receipt")
    identifier = proof(request)
    value = f"v1 {identifier}\n" if identifier else None
    if action == "record":
        if value is None:
            raise ValueError("Editor dependencies changed after the headless check")
        atomic_write(path, value.encode(), 0o600, preserve_existing_mode=False)
    return value is not None and value == previous


class EditorTask(StrEnum):
    CHECK = "check"
    RECORD = "record"


cli = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def verify(action: EditorTask) -> None:
    """Verify or record an editor receipt."""
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Editor proof exceeds the supported size")
    print(json.dumps(run(action, TypeAdapter(EditorInputs).validate_json(raw))))


if __name__ == "__main__":
    cli()
