"""Install the built wheel in a disposable environment outside the source tree."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


def verify_resources(root: Path, wheel: Path, sdist: Path) -> None:
    """Compare source, wheel and sdist bytes for every packaged runtime resource."""
    expected: dict[str, bytes] = {}
    folders = ("assets", "config", "mk", "libexec", "ansible", "docs")
    for name in ("Makefile", "ansible.cfg", "install.sh"):
        expected[name] = (root / name).read_bytes()
    for directory in folders:
        for file in (root / directory).rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
                assert not file.is_symlink(), file
                expected[file.relative_to(root).as_posix()] = file.read_bytes()
    prefixes = tuple(directory + "/" for directory in folders)
    with tarfile.open(sdist, "r:gz") as source, zipfile.ZipFile(wheel) as dist:
        prefix = sdist.name.removesuffix(".tar.gz") + "/"
        native = {
            member.name.removeprefix(prefix) for member in source.getmembers() if member.isfile()
        }
        wheel_members = {
            name.removeprefix("setup_system/bundle/")
            for name in dist.namelist()
            if name.startswith("setup_system/bundle/") and not name.endswith("/")
        }
        source_members = {name for name in native if name.startswith(prefixes) or name in expected}
        if source_members != expected.keys() or wheel_members != expected.keys():
            raise ValueError(
                f"Source/resource mismatch: sdist={source_members ^ expected.keys()}, "
                f"wheel={wheel_members ^ expected.keys()}"
            )
        for path, data in expected.items():
            member = source.extractfile(prefix + path)
            if (
                member is None
                or member.read() != data
                or dist.read("setup_system/bundle/" + path) != data
            ):
                raise ValueError("Distributions contain different runtime bytes: " + path)


def verify(artifacts: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    artifacts = artifacts.resolve(strict=True)
    wheels = sorted(artifacts.glob("*.whl"))
    sdists = sorted(artifacts.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected one wheel and one sdist; choose a fresh artifact directory")
    verify_resources(root, wheels[0], sdists[0])
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
