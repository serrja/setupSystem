"""Locate runtime resources and verify the installed Debian program before become."""

import subprocess
from pathlib import Path

from setup_system_sdk.files.system_paths import protected
from setup_system_sdk.processes import run_command

PACKAGE = "setup-system"
INSTALLED = Path("/usr/lib") / PACKAGE
PYTHON_PACKAGE = Path("/usr/lib/python3/dist-packages/setup_system")
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
    """Compare both executable trees with dpkg's inventory, checksums and permissions."""
    if root != INSTALLED:
        raise ValueError("System changes require the installed Debian package")
    owned = _package_paths()
    parent = PYTHON_PACKAGE.parent
    metadata = [p for p in owned if p.parent == parent and p.match("setup_system-*.dist-info")]
    if len(metadata) != 1 or set(parent.glob("setup_system-*.dist-info")) != set(metadata):
        raise ValueError("Python distribution metadata differs from the Debian package inventory")
    sdk = parent / "setup_system_sdk"
    for tree in (root, PYTHON_PACKAGE, sdk, parent / "setup_plugin_debian", *metadata):
        protected(tree)
        expected = {p for p in owned if p.is_relative_to(tree)}
        actual = {tree, *tree.rglob("*")}
        if tree not in expected or actual != expected:
            raise ValueError("Program resources differ from the Debian package inventory")
        for entry in actual:
            protected(entry, directory=entry.is_dir())
    if not (root / "Makefile").is_file() or not all(
        path.is_file()
        for path in (PYTHON_PACKAGE / "entrypoint.py", sdk / "worker.py", metadata[0] / "METADATA")
    ):
        raise ValueError("The installed Debian program is incomplete")


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
