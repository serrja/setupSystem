"""Validate the exact native removal closure and the alternative administrative editor."""

import json
import os
import pwd
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict

from pydantic import ConfigDict, TypeAdapter, with_config

from setup_system.domain.contracts import MAX_MESSAGE_BYTES
from setup_system.domain.intentions import CleanupMode
from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.processes import run_command
from setup_system.layout import require_installed_resource


@with_config(ConfigDict(extra="forbid", strict=True))
class RemovalSelection(TypedDict):
    selected: list[str]
    closure: list[str]
    mode: str
    home: str
    xdg: dict[str, str]


def surviving_editor(path: Path, closure: set[str]) -> bool:
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return False
    try:
        owner = run_command(
            ["/usr/bin/dpkg-query", "-S", str(resolved)],
            stdin=subprocess.DEVNULL,
            discard_stderr=True,
            timeout=15,
        ).stdout
    except subprocess.CalledProcessError:
        return False
    row = re.fullmatch(r"([a-z0-9][a-z0-9.+-]+)(?::[a-z0-9-]+)?: ([^\n]+)\n?", owner)
    return bool(row and row[1] not in closure and row[2] == str(resolved))


def installed_candidates(requested: set[str], architecture: str, mode: CleanupMode) -> set[str]:
    """Read all installed architectures for exactly the selected package names."""
    query = run_command(
        [
            "/usr/bin/dpkg-query",
            "-W",
            "-f=${Package}|${Architecture}|${db:Status-Status}|${db:Status-Want}|${Essential}|${Protected}\n",
            "--",
            *sorted(requested),
        ],
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    observed = set()
    installed = set()
    for line in query.stdout.splitlines():
        name, arch, state, want, essential, protected = line.split("|")
        if name not in requested:
            raise ValueError("Unexpected package in the native inventory")
        observed.add(name)
        if state == "installed" or (state == "config-files" and mode is CleanupMode.PURGE):
            if arch not in {"all", architecture}:
                raise ValueError("Unexpected architecture in the installed removal selection")
            if name in installed or want == "hold" or "yes" in (essential, protected):
                raise ValueError("A held, protected or ambiguous package cannot be removed")
            installed.add(name)
    missing = requested - observed
    if query.returncode == 1 and missing:
        diagnostics = {f"dpkg-query: no packages found matching {name}" for name in missing}
        if set(query.stderr.splitlines()) != diagnostics:
            raise ValueError("Cannot verify the missing removal candidates")
    else:
        query.check_returncode()
        if missing:
            raise ValueError("Incomplete native package inventory")
    return installed


def check(request: RemovalSelection) -> None:
    architecture, supported = current_target()
    if os.geteuid() == 0 and not supported:
        raise ValueError("Native removal requires a supported Debian target")
    try:
        mode = CleanupMode(request["mode"])
    except ValueError as exc:
        raise ValueError("Unknown removal mode") from exc
    action = "Purg" if mode is CleanupMode.PURGE else "Remv"
    package = re.compile(rf"{action} ([a-z0-9][a-z0-9+.-]*)(?::([a-z0-9-]+))?(?: \[[^]]+\])?")
    closure = set()
    for line in request["closure"]:
        match = package.fullmatch(line)
        if not match or (match[2] and match[2] != architecture) or match[1] in closure:
            raise ValueError("Unexpected native APT removal record")
        closure.add(match[1])
    root = Path(__file__).resolve().parents[2]
    catalog = tomllib.loads((root / "config/workspace/optional_cleanup.toml").read_text())[
        "candidates"
    ]
    requested = set(request["selected"])
    if not requested or not requested <= catalog.keys():
        raise ValueError("Choose only reviewed removal candidates")
    if closure != installed_candidates(requested, architecture, mode):
        raise ValueError("APT would remove packages outside the confirmed selection")
    if any(catalog[name].get("requires_verified_editor") for name in closure):
        backup = Path("/usr/bin/editor").resolve(strict=True)
        alternatives = [
            p
            for p in (
                Path("/usr/bin/vi"),
                Path("/usr/bin/ex"),
                Path("/usr/bin/ed"),
                Path("/usr/bin/nano"),
                Path("/usr/bin/vim.basic"),
            )
            if p.is_file()
        ]
        if not any(
            p.resolve() != Path("/usr/bin/nvim").resolve() and surviving_editor(p, closure)
            for p in alternatives
        ):
            raise ValueError("An independent installed administrative editor is required")
        nvim = Path("/usr/bin/nvim")
        if not nvim.is_file() or (
            backup != nvim.resolve() and not surviving_editor(backup, closure)
        ):
            raise ValueError("Verify the system editor alternatives before removal")
        home = Path(request["home"])
        uid = int(os.environ.get("SUDO_UID", os.getuid())) if os.geteuid() == 0 else os.getuid()
        account = pwd.getpwuid(uid)
        config = Path(request["xdg"]["config"])
        if (
            not uid
            or str(home) != account.pw_dir
            or not config.is_relative_to(home)
            or ".." in config.parts
        ):
            raise ValueError("Unknown editor account")
        commands = ["/usr/sbin/runuser", "-u", account.pw_name, "--"] if os.geteuid() == 0 else []
        keys = ("EDITOR=nvim", "VISUAL=nvim")
        matched = run_command(
            [
                *commands,
                "/usr/bin/grep",
                "-Fx",
                "-e",
                keys[0],
                "-e",
                keys[1],
                "--",
                str(config / "environment.d/90-setup-system-editor.conf"),
            ],
            timeout=5,
            discard_stderr=True,
            stdin=subprocess.DEVNULL,
        )
        if set(matched.stdout.splitlines()) != set(keys):
            raise ValueError("Verify both Neovim editor environment settings before removal")
        run_command(
            [*commands, "/usr/bin/nvim", "--clean", "--headless", "-n", "-i", "NONE", "+q"],
            timeout=15,
            stdin=subprocess.DEVNULL,
            discard_output=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        )
    print(json.dumps(sorted(closure)))


if __name__ == "__main__":
    if os.geteuid() == 0:
        require_installed_resource(Path(__file__), "ansible/files/removals.py")
    raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Removal selection exceeds the supported size")
    check(TypeAdapter(RemovalSelection).validate_json(raw))
