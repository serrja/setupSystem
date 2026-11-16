"""Private delivery adapter: persist verified source and prepare its runtime as a user."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from setup_system.installation import resolve_target, user_environment, validate_user_path
from setup_system.runtime import UserRuntime


def prepare(source: Path, release: str, uv: Path) -> tuple[Path, Path]:
    """Confine environment changes to this private delivery operation."""
    previous = dict(os.environ)
    try:
        return _prepare(source, release, uv)
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _prepare(source: Path, release: str, uv: Path) -> tuple[Path, Path]:
    if os.geteuid() == 0:
        raise ValueError("Delivery builds must run as the desktop user")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?", release):
        raise ValueError("An explicit release tag is required")
    target = resolve_target(None)
    environment = user_environment(target, {})
    os.environ.update(environment)
    data = Path(environment["XDG_DATA_HOME"])
    parent = validate_user_path(data / "setup-system/releases", target)
    destination = validate_user_path(parent / release, target)
    entries = UserRuntime(source).inputs()
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = UserRuntime(destination).inputs()
        if entries != existing or any(
            hashlib.sha256((source / path).read_bytes()).digest()
            != hashlib.sha256((destination / path).read_bytes()).digest()
            for path, directory in entries
            if not directory
        ):
            raise ValueError("Existing release source differs; preserve it for review")
    else:
        with tempfile.TemporaryDirectory(prefix=".delivery-", dir=parent) as temporary:
            stage = Path(temporary) / release
            stage.mkdir()
            for relative, directory in entries:
                output = stage / relative
                if directory:
                    output.mkdir()
                else:
                    shutil.copy2(source / relative, output)
            stage.rename(destination)
    binary = validate_user_path(data / "setup-system/bin/uv", target)
    binary.parent.mkdir(parents=True, exist_ok=True)
    info = uv.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != target.uid:
        raise ValueError("Verified uv candidate must be a user-owned regular file")
    if binary.exists():
        if binary.read_bytes() != uv.read_bytes():
            raise ValueError("Existing managed uv differs; preserve it for review")
    else:
        with binary.open("xb") as stream:
            stream.write(uv.read_bytes())
        binary.chmod(0o755)
    runtime = UserRuntime(destination).prepare()
    return destination, runtime / "bin/setup-system"


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Invalid private delivery request")
    prepare(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
