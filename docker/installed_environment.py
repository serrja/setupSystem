"""Verify the installed host and its workspace dependencies outside the checkout."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer


def main(dist: Annotated[Path, typer.Argument()]) -> None:
    dist = dist.resolve()
    checker = Path(__file__).with_name("verify-installed.py").resolve()
    (theme,) = dist.glob("setup_system_desktop_theme-*.whl")
    (sdk,) = dist.glob("setup_system_sdk-*.whl")
    (host,) = [
        p
        for p in dist.glob("setup_system-*.whl")
        if p != sdk and p.name.split("-")[0] == "setup_system"
    ]
    with tempfile.TemporaryDirectory() as temp:
        cwd = Path(temp)
        python = cwd / "venv/bin/python"
        requirements = cwd / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-workspace",
                "--no-header",
                "--output-file",
                str(requirements),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(python.parents[1])], cwd=cwd, check=True
        )
        subprocess.run(
            ["uv", "pip", "sync", "--require-hashes", "--python", str(python), str(requirements)],
            cwd=cwd,
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--link-mode",
                "copy",
                "--python",
                str(python),
                str(theme),
                str(sdk),
                str(host),
            ],
            cwd=cwd,
            check=True,
        )
        subprocess.run(
            [str(python), "-I", "-B", str(checker), "--wheel", str(host)],
            cwd=cwd,
            env={
                **os.environ,
                "HOME": str(cwd),
                "PATH": str(python.parent) + os.pathsep + os.environ["PATH"],
            },
            check=True,
        )


if __name__ == "__main__":
    typer.run(main)
