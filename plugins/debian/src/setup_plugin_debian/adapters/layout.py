"""Locate runtime resources and verify the installed Debian program before become."""

import os
import re
import stat
import subprocess
from pathlib import Path

from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.processes import run_command

PACKAGE = "setup-system"
INSTALLED = Path("/usr/lib") / PACKAGE
RUNTIME_STATE = Path("/var/lib/setup-system")
NATIVE_PYTHON = RUNTIME_STATE / "active/native/bin/python"
NATIVE_ANSIBLE = RUNTIME_STATE / "active/native/bin/ansible-playbook"
GUIDES = Path("/usr/share/doc") / PACKAGE / "docs"


def resource_root() -> Path:
    return INSTALLED


def _query(*args: str) -> str:
    try:
        return run_command(
            list(args),
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            cwd="/",
            stdin=subprocess.DEVNULL,
            discard_stderr=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Could not verify the installed Debian package") from exc


def _package_paths() -> set[Path]:
    status = _query("/usr/bin/dpkg-query", "-W", "-f=${Status}", PACKAGE)
    if status not in {"install ok installed", "hold ok installed"}:
        raise ValueError("The Debian package must be configured")
    if _query("/usr/bin/dpkg", "--verify", PACKAGE):
        raise ValueError("The Debian package failed its integrity check")
    return {Path(name) for name in _query("/usr/bin/dpkg-query", "-L", PACKAGE).splitlines()}


def verify_installation(root: Path) -> None:
    """Compare package-owned resources with dpkg inventory and permissions."""
    if root != INSTALLED:
        raise ValueError("System changes require the installed Debian package")
    owned = _package_paths()
    for tree in (root,):
        protected(tree)
        expected = {p for p in owned if p.is_relative_to(tree)}
        actual = {tree, *tree.rglob("*")}
        if tree not in expected or actual != expected:
            raise ValueError("Program resources differ from the Debian package inventory")
        for entry in actual:
            protected(entry, directory=entry.is_dir())
    if not (root / "Makefile").is_file() or not all(
        path.is_file()
        for path in (
            root / "runtime/source",
            root / "runtime/host-amd64.txt",
            root / "runtime/native-arm64.txt",
        )
    ):
        raise ValueError("The installed Debian program is incomplete")
    source = (root / "runtime/source").read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError("The runtime source identity is invalid")
    active = RUNTIME_STATE / "active"
    pointer = active.lstat()
    if not stat.S_ISLNK(pointer.st_mode) or pointer.st_uid != 0:
        raise ValueError("The active runtime pointer is untrusted")
    generation = active.resolve(strict=True)
    if generation != RUNTIME_STATE / "runtimes" / source:
        raise ValueError("The active runtime differs from the installed source")
    for directory in (RUNTIME_STATE, RUNTIME_STATE / "runtimes", generation):
        protected(directory)
    ready = generation / "ready"
    protected(ready, directory=False)
    if ready.read_text().strip() != source:
        raise ValueError("The native runtime is not prepared")
    for executable in (generation / "host/bin/python", generation / "native/bin/python"):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("The private Python runtime is incomplete")
    protected(generation / "native/bin/ansible-playbook", directory=False)


def require_installed_resource(source: Path, relative: str) -> None:
    """Check the identity and complete package before a fixed privileged helper runs."""
    if (
        source != INSTALLED / relative
        or not source.is_relative_to(INSTALLED)
        or ".." in source.parts
    ):
        raise ValueError("This helper requires the installed Debian package")
    verify_installation(INSTALLED)
    protected(source, directory=False)
    if not source.is_file():
        raise ValueError("The installed Debian helper is missing")
