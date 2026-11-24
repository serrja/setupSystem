"""Validate the exact native removal closure and the alternative administrative editor."""

import json
import os
import pwd
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from setup_system.infrastructure.native_target import current_target


def surviving_editor(path, closure):
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return False
    try:
        owner = subprocess.check_output(["/usr/bin/dpkg-query", "-S", str(resolved)], text=True)
    except subprocess.CalledProcessError:
        return False
    row = re.fullmatch(r"([a-z0-9][a-z0-9.+-]+)(?::[a-z0-9-]+)?: ([^\n]+)\n?", owner)
    return bool(row and row[1] not in closure and row[2] == str(resolved))


def check(request):
    architecture, supported = current_target()
    if os.geteuid() == 0 and not supported:
        raise ValueError("Native removal requires a supported Debian target")
    mode = request["mode"]
    if mode not in {"remove", "purge", "autoremove"}:
        raise ValueError("Unknown removal mode")
    action = "Purg" if mode == "purge" else "Remv"
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
    query = subprocess.run(
        [
            "/usr/bin/dpkg-query",
            "-W",
            "-f=${binary:Package}|${db:Status-Status}|${db:Status-Want}|${Essential}|${Protected}\n",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    installed = set()
    for line in query.stdout.splitlines():
        name, state, want, essential, protected = line.split("|")
        name, _, arch = name.partition(":")
        if name not in requested:
            continue
        if state == "installed" or (state == "config-files" and request["mode"] == "purge"):
            if arch and arch != architecture:
                raise ValueError("Unexpected architecture in the installed removal selection")
            if name in installed or want == "hold" or "yes" in (essential, protected):
                raise ValueError("A held, protected or ambiguous package cannot be removed")
            installed.add(name)
    if closure != installed:
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
        subprocess.run(
            [
                *commands,
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "lines=p.read_text().splitlines(); "
                "sys.exit(0 if 'EDITOR=nvim' in lines and 'VISUAL=nvim' in lines else 1)",
                str(config / "environment.d/90-setup-system-editor.conf"),
            ],
            check=True,
        )
        subprocess.run(
            [*commands, "/usr/bin/nvim", "--clean", "--headless", "-n", "-i", "NONE", "+q"],
            timeout=15,
            check=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        )
    print(json.dumps(sorted(closure)))


if __name__ == "__main__":
    check(json.load(sys.stdin))
