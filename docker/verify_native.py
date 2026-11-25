"""Install the built wheel in a disposable environment outside the source tree."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from setup_system.layout import GUIDES, INSTALLED, PACKAGE, PYTHON_PACKAGE


def verify_resources(root: Path, wheel: Path, sdist: Path, deb: Path) -> None:
    """Check that every distribution carries the source resources at its runtime paths."""
    expected: dict[str, bytes] = {}
    folders = ("assets", "config", "mk", "libexec", "ansible", "docs")
    for name in ("Makefile", "ansible.cfg", "install.sh"):
        expected[name] = (root / name).read_bytes()
    for directory in folders:
        for file in (root / directory).rglob("*"):
            if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
                assert not file.is_symlink(), file
                expected[file.relative_to(root).as_posix()] = file.read_bytes()
    modules = {
        path.relative_to(root / "src").as_posix(): path.read_bytes()
        for path in (root / "src/setup_system").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    contents = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", deb], timeout=60)
    packaged = {
        (GUIDES.parent if path.startswith("docs/") else INSTALLED) / path: data
        for path, data in expected.items()
    }
    packaged.update({PYTHON_PACKAGE.parent / path: data for path, data in modules.items()})
    with tarfile.open(fileobj=io.BytesIO(contents)) as archive:
        found = set()
        for entry in archive:
            debian_path = Path("/") / entry.name
            if entry.isdir() or not any(
                debian_path.is_relative_to(location)
                for location in (INSTALLED, PYTHON_PACKAGE, GUIDES)
            ):
                continue
            if not entry.isfile() or debian_path not in packaged or debian_path in found:
                raise ValueError(f"Unexpected Debian payload member: {debian_path}")
            member = archive.extractfile(entry)
            if member is None or member.read() != packaged[debian_path]:
                raise ValueError(f"Debian payload differs from source: {debian_path}")
            found.add(debian_path)
        if missing := packaged.keys() - found:
            raise ValueError(f"Debian payload is incomplete: {sorted(missing)}")

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
        wheel_modules = {
            name
            for name in dist.namelist()
            if name.startswith("setup_system/")
            and not name.startswith("setup_system/bundle/")
            and not name.endswith("/")
        }
        source_modules = {
            name.removeprefix("src/") for name in native if name.startswith("src/setup_system/")
        }
        if source_modules != modules.keys() or wheel_modules != modules.keys():
            raise ValueError("Distributions contain unexpected Python package members")
        for path, data in modules.items():
            module = source.extractfile(prefix + "src/" + path)
            if module is None or module.read() != data or dist.read(path) != data:
                raise ValueError("Distributions contain different Python modules: " + path)
        for path, data in expected.items():
            member = source.extractfile(prefix + path)
            if (
                member is None
                or member.read() != data
                or dist.read("setup_system/bundle/" + path) != data
            ):
                raise ValueError("Distributions contain different runtime bytes: " + path)


def verify(artifacts: Path, debian_dir: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    artifacts = artifacts.resolve(strict=True)
    wheels = sorted(artifacts.glob("*.whl"))
    sdists = sorted(artifacts.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected one wheel and one sdist; choose a fresh artifact directory")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    packages = [
        path
        for path in debian_dir.glob(f"{PACKAGE}_*_all.deb")
        if subprocess.check_output(
            ["dpkg-deb", "-f", path, "X-Setup-System-Source"], text=True
        ).strip()
        == sha
    ]
    if len(packages) != 1:
        raise ValueError("Expected one compiled Debian archive for current HEAD")
    verify_resources(root, wheels[0], sdists[0], packages[0])
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
    parser.add_argument("debian_dir", type=Path)
    args = parser.parse_args()
    verify(args.artifacts, args.debian_dir)


if __name__ == "__main__":
    main()
