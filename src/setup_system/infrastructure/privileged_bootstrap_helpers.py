"""Check the user's editor configuration and executable under their ordinary UID."""

import subprocess
from pathlib import Path

from setup_system.infrastructure.trusted_bootstrap import caller

# The child reads bounded user files only after runuser has dropped privilege.
_EDITOR_CHECK = """
import json
import os
import pathlib
import stat
import subprocess
import sys

home, config = map(pathlib.Path, sys.argv[1:])
def read(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Unexpected editor configuration path")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4096:
            raise ValueError("Invalid editor configuration")
        return stream.read(4097)

try:
    marker = json.loads(read(config / "setup-system/editor.json"))
    env = read(config / "environment.d/90-setup-system-editor.conf").decode().splitlines()
    if (marker.get("EDITOR") != "nvim" or marker.get("VISUAL") != "nvim"
        or "EDITOR=nvim" not in env or "VISUAL=nvim" not in env):
        raise ValueError("Incomplete editor integration")
    binary = home / ".local/bin/nvim"
    if binary.is_file():
        if not binary.resolve().is_relative_to(home):
            raise ValueError("Unexpected editor path")
    else:
        binary = pathlib.Path("/usr/bin/nvim")
    subprocess.run([str(binary), "--clean", "--headless", "-n", "-i", "NONE", "+q"],
                   check=True, timeout=20, stdin=subprocess.DEVNULL)
except (OSError, ValueError, subprocess.SubprocessError):
    sys.exit(1)
"""


def editor_ready(username: str, config_home: Path | None = None) -> bool:
    account = caller()
    if username != account.pw_name:
        return False
    home = Path(account.pw_dir)
    config = config_home or home / ".config"
    if not config.is_relative_to(home) or ".." in config.parts:
        return False
    result = subprocess.run(
        [
            "/usr/sbin/runuser",
            "-u",
            username,
            "--",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-S",
            "-c",
            _EDITOR_CHECK,
            str(home),
            str(config),
        ],
        check=False,
        capture_output=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": account.pw_dir},
    )
    return result.returncode == 0
