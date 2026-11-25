"""Validate exact native APT removals and read back dpkg status."""

from __future__ import annotations

import fcntl
import hashlib
import os
import pwd
import re
import subprocess
import sys
import tomllib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from setup_system.infrastructure.native_target import current_target as current_target
from setup_system.infrastructure.processes import run_command
from setup_system.layout import require_installed_resource


class Mode(StrEnum):
    REMOVE = "remove"
    PURGE = "purge"
    AUTOREMOVE = "autoremove"


_ACTION = re.compile(r"(Remv|Purg) ([a-z0-9][a-z0-9+.-]*)(?::([a-z0-9-]+))?(?: \[([^\]]+)\])?")
FRONTEND_LOCK = Path("/var/lib/dpkg/lock-frontend")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/"}


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reason: str
    requires_verified_editor: bool = False


class Package(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    version: str
    architecture: str
    status: str


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Mode
    selected: list[str] = Field(min_length=1, max_length=64)
    caller_uid: int = Field(gt=0)
    config_home: str
    expected: str = Field(pattern=r"^[a-f0-9]{64}$")


def catalog() -> dict[str, Candidate]:
    with Path(__file__).with_name("candidates.toml").open("rb") as stream:
        return TypeAdapter(dict[str, Candidate]).validate_python(tomllib.load(stream)["candidates"])


def command(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(list(argv), env=ENV, stdin=subprocess.DEVNULL, check=check, timeout=30)


def inventory(names: list[str], mode: Mode, arch: str) -> list[Package]:
    query = command(
        "/usr/bin/dpkg-query",
        "-W",
        "-f=${Package}|${Architecture}|${db:Status-Status}|${db:Status-Want}|${Essential}|${Protected}|${Version}\\n",
        "--",
        *names,
        check=False,
    )
    observed = set()
    result = []
    for row in query.stdout.splitlines():
        name, architecture, status, want, essential, protected, version = row.split("|")
        if name not in names or name in observed or architecture not in (arch, "all"):
            raise ValueError("Ambiguous package identity in dpkg inventory.")
        observed.add(name)
        if status in ("not-installed", "config-files") and not (
            mode == Mode.PURGE and status == "config-files"
        ):
            continue
        if (
            status not in ("installed", "config-files")
            or want == "hold"
            or "yes" in (essential, protected)
        ):
            raise ValueError(
                "A held, protected, essential or half-configured package cannot be removed."
            )
        result.append(Package(name=name, architecture=architecture, status=status, version=version))
    missing = set(names) - observed
    if missing:
        if query.returncode != 1 or set(query.stderr.splitlines()) != {
            f"dpkg-query: no packages found matching {name}" for name in missing
        }:
            raise ValueError("Cannot verify the complete dpkg removal inventory.")
    else:
        query.check_returncode()
    return sorted(result, key=lambda p: p.name)


def verify_simulation(text: str, packages: list[Package], mode: Mode) -> None:
    found = {}
    for line in text.splitlines():
        if not line.startswith(("Inst ", "Conf ", "Remv ", "Purg ")):
            continue
        match = _ACTION.fullmatch(line)
        if not match or match[1] != ("Purg" if mode == Mode.PURGE else "Remv") or match[2] in found:
            raise ValueError("The APT plan contains an unexpected action.")
        found[match[2]] = (match[3], match[4])
    if set(found) != {p.name for p in packages}:
        raise ValueError("APT would act outside the selected removal closure.")
    for p in packages:
        arch, version = found[p.name]
        if (arch and arch != p.architecture) or (version and version != p.version):
            raise ValueError("Installed package or architecture differs from the APT plan.")


def surviving_editor(path: Path, removed: set[str]) -> bool:
    target = path.resolve()
    if not target.is_file() or not os.access(target, os.X_OK):
        return False
    proof = command("/usr/bin/dpkg-query", "-S", str(target), check=False)
    match = re.fullmatch(r"([a-z0-9][a-z0-9.+-]+)(?::[a-z0-9-]+)?: ([^\n]+)\n?", proof.stdout)
    return bool(
        not proof.returncode and match and match[1] not in removed and match[2] == str(target)
    )


def verify_editors(removed: set[str], uid: int, config: Path) -> None:
    account = pwd.getpwuid(uid)
    if uid == 0 or not config.is_relative_to(Path(account.pw_dir)) or ".." in config.parts:
        raise ValueError("Select the ordinary user's verified editor configuration.")
    nvim = Path("/usr/bin/nvim")
    backup = [Path("/usr/bin") / name for name in ("vi", "ex", "ed", "nano", "vim.basic")]
    if not surviving_editor(nvim, removed) or not surviving_editor(
        Path("/usr/bin/editor"), removed
    ):
        raise ValueError("A working primary Neovim and a surviving system editor are required.")
    if not any(p.resolve() != nvim.resolve() and surviving_editor(p, removed) for p in backup):
        raise ValueError("An independent installed backup editor is required.")
    prefix = ["/usr/sbin/runuser", "-u", account.pw_name, "--"] if os.geteuid() == 0 else []
    keys = {"EDITOR=nvim", "VISUAL=nvim"}
    matched = command(
        *prefix,
        "/usr/bin/grep",
        "-Fx",
        "-e",
        "EDITOR=nvim",
        "-e",
        "VISUAL=nvim",
        "--",
        str(config / "environment.d/90-setup-system-editor.conf"),
    )
    if set(matched.stdout.splitlines()) != keys:
        raise ValueError("Verify EDITOR and VISUAL for the selected account.")
    command(*prefix, "/usr/bin/nvim", "--clean", "--headless", "-n", "-i", "NONE", "+q")


def preview(mode: Mode, selected: list[str], uid: int, config: Path) -> list[Package]:
    arch, supported = current_target()
    if not supported:
        raise ValueError("This removal policy requires Debian 13.")
    candidates = catalog()
    if (
        not selected
        or len(selected) != len(set(selected))
        or not set(selected) <= candidates.keys()
    ):
        raise ValueError("Choose distinct reviewed package candidates.")
    packages = inventory(selected, mode, arch)
    selection = [p.name for p in packages]
    if packages or mode == Mode.AUTOREMOVE:
        args = [] if mode == Mode.AUTOREMOVE else ["--", *selection]
        response = command("/usr/bin/apt-get", "--simulate", mode, *args)
        verify_simulation(response.stdout, packages, mode)
    if any(candidates[name].requires_verified_editor for name in selection):
        verify_editors(set(selection), uid, config)
    return packages


def digest(packages: list[Package]) -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_bytes()
    return hashlib.sha256(boot + TypeAdapter(list[Package]).dump_json(packages)).hexdigest()


def apply(confirmation: Confirmation) -> None:
    if os.geteuid() != 0:
        raise ValueError("Authenticate with sudo to remove the confirmed package closure.")
    require_installed_resource(Path(__file__), "plugins/cleanup/setup_plugin_cleanup/native.py")
    require_installed_resource(
        Path(__file__).with_name("candidates.toml"),
        "plugins/cleanup/setup_plugin_cleanup/candidates.toml",
    )
    # Keep the native frontend lock across revalidation, exact-name dpkg and readback.
    # dpkg still acquires its own backend database lock in the child.
    descriptor = os.open(FRONTEND_LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "r+b") as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected = preview(
            confirmation.mode,
            confirmation.selected,
            confirmation.caller_uid,
            Path(confirmation.config_home),
        )
        if not selected or digest(selected) != confirmation.expected:
            raise ValueError("The APT simulation or package status has changed. Preview again.")
        # dpkg acts on exactly these names and retains its native dependency checks.
        # No APT resolver can add a package between the recheck and dpkg execution.
        subprocess.run(
            [
                "/usr/bin/dpkg",
                "--purge" if confirmation.mode == Mode.PURGE else "--remove",
                "--",
                *[p.name for p in selected],
            ],
            env={**ENV, "DPKG_FRONTEND_LOCKED": "true"},
            stdin=subprocess.DEVNULL,
            check=True,
        )
        if inventory(confirmation.selected, confirmation.mode, current_target()[0]):
            raise ValueError("dpkg has not completed the confirmed removal.")


if __name__ == "__main__":
    data = sys.stdin.buffer.read(32769)
    if len(data) > 32768:
        sys.exit(2)
    apply(Confirmation.model_validate_json(data))
