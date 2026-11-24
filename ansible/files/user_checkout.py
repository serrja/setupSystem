"""Recognize a previously selected user checkout before asking Git to change it."""

import os
import subprocess
import sys
from pathlib import Path

from setup_system.infrastructure.tool_paths import personal_path


def observe(path, repo):
    home = Path(os.environ["HOME"])
    if (
        not path.is_relative_to(home)
        or ".." in path.parts
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise ValueError("Unsafe user tool directory")
    personal_path(home, path)
    if not path.exists():
        return ""
    if not (path / ".git").is_dir() or (path / ".git").is_symlink():
        raise ValueError("An existing user tool directory requires review")

    def git(*args):
        return subprocess.check_output(
            ["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
            text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        ).strip()

    if git("remote", "get-url", "origin") != repo or git(
        "status", "--porcelain", "--untracked-files=normal"
    ):
        raise ValueError("A user tool checkout has local edits or a different origin")
    return git("rev-parse", "HEAD")


if __name__ == "__main__":
    print(observe(Path(sys.argv[1]), sys.argv[2]))
