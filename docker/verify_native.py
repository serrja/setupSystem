"""Install the built wheel in a disposable environment outside the source tree."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def verify(artifacts: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    artifacts = artifacts.resolve(strict=True)
    wheels = sorted(artifacts.glob("*.whl"))
    sdists = sorted(artifacts.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected one wheel and one sdist; choose a fresh artifact directory")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="setup-system-installed-") as temporary:
        stage = Path(temporary)
        executable = stage / "venv/bin/python"
        requirements = stage / "runtime.txt"
        commands = (
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(requirements),
                "--quiet",
            ],
            ["uv", "venv", "--python", sys.executable, str(stage / "venv")],
            [
                "uv",
                "pip",
                "sync",
                "--require-hashes",
                "--python",
                str(executable),
                str(requirements),
            ],
            [
                "uv",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--python",
                str(executable),
                str(wheels[0]),
            ],
        )
        for command in commands:
            subprocess.run(command, cwd=root, env=environment, timeout=300, check=True)
        environment["PATH"] = str(executable.parent) + os.pathsep + environment["PATH"]
        (stage / "pyproject.toml").write_bytes((root / "pyproject.toml").read_bytes())
        subprocess.run(
            [
                str(executable),
                "-I",
                "-B",
                str(root / "docker/verify-installed.py"),
                "--wheel",
                str(wheels[0]),
            ],
            cwd=stage,
            env=environment,
            timeout=120,
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()
    verify(args.artifacts)


if __name__ == "__main__":
    main()
