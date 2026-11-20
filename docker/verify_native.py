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
    for directory in ("assets", "manifests", "src/setup_system/resources"):
        for file in (root / directory).rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts:
                expected[file.relative_to(root).as_posix()] = file.read_bytes()
    with tarfile.open(sdist, "r:gz") as source, zipfile.ZipFile(wheel) as dist:
        prefix = sdist.name.removesuffix(".tar.gz")
        source_names = {
            item.name.removeprefix(prefix + "/") for item in source.getmembers() if item.isfile()
        }
        source_resources = {
            name
            for name in source_names
            if name.startswith(("assets/", "manifests/", "src/setup_system/resources/"))
        }
        if source_resources != expected.keys():
            raise ValueError(
                f"Unexpected sdist resource inventory: {source_resources ^ expected.keys()}"
            )
        for path, data in expected.items():
            package_path = (
                path.removeprefix("src/") if path.startswith("src/") else "setup_system/" + path
            )
            member = source.extractfile(prefix + "/" + path)
            if member is None or member.read() != data or dist.read(package_path) != data:
                raise ValueError("Resource mismatch between source and distributions: " + path)
        names = {p for p in dist.namelist() if not p.endswith("/")}
        actual = {
            p.removeprefix("setup_system/")
            if not p.startswith("setup_system/resources/")
            else "src/" + p
            for p in names
            if p.startswith(
                ("setup_system/assets/", "setup_system/manifests/", "setup_system/resources/")
            )
        }
        if actual != expected.keys():
            raise ValueError(f"Unexpected wheel resource inventory: {actual ^ expected.keys()}")


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
