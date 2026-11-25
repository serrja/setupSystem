"""Install the built wheel in a disposable environment outside the source tree."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from setup_system.extensions import Release, wheel_manifest
from setup_system.layout import GUIDES, INSTALLED, PACKAGE, PYTHON_PACKAGE


def verify_resources(root: Path, wheel: Path, sdist: Path, deb: Path) -> None:
    """Check that every distribution carries the source resources at its runtime paths."""
    files = [
        Path(os.fsdecode(p))
        for p in subprocess.check_output(["git", "ls-files", "-z"], cwd=root).split(b"\0")
        if p
    ]
    shared = {"config", "docs", "libexec", "mk", "profiles"}
    expected = {
        p.as_posix(): (root / p).read_bytes()
        for p in files
        if p.parts[0] in shared or p.as_posix() in {"Makefile", "ansible.cfg", "install.sh"}
    }
    modules = {
        p.relative_to("src").as_posix(): (root / p).read_bytes()
        for p in files
        if p.parts[:2] in {("src", "setup_system"), ("src", "setup_system_sdk")}
    }
    plugin_source = {
        p.as_posix(): (root / p).read_bytes() for p in files if p.parts[0] == "plugins"
    }
    with zipfile.ZipFile(wheel) as dist:
        members = {name: dist.read(name) for name in dist.namelist() if not name.endswith("/")}
    prefix = "setup_system/bundle/"
    catalog = {
        p.removeprefix(prefix + "plugin_catalog/"): b
        for p, b in members.items()
        if p.startswith(prefix + "plugin_catalog/")
    }
    projects = list((root / "plugins").glob("*/*/pyproject.toml"))
    if not projects or len(catalog) != 2 * len(projects):
        raise ValueError("Unexpected number of plugin release files")
    plugin_assets: dict[str, bytes] = {}
    for project in projects:
        identity = project.parent.name
        selected = Release(catalog[identity + ".json"]).selected()
        (primary,) = selected.wheels
        payload = catalog[primary.name]
        if (
            hashlib.sha256(payload).hexdigest() != primary.sha256
            or wheel_manifest(primary.name, payload) != selected.manifest
            or selected.manifest.id != identity
        ):
            raise ValueError("Built plugin release differs from its wheel")
        module = selected.manifest.entry_point.partition(":")[0]
        relative = next(
            (d / module).relative_to(root)
            for d in (project.parent / "src", project.parent)
            if (d / module / "manifest.json").is_file()
        )
        sources = {
            p.relative_to(relative).as_posix(): plugin_source[p.as_posix()]
            for p in files
            if p.is_relative_to(relative)
        }
        with zipfile.ZipFile(io.BytesIO(payload)) as plugin:
            actual = {
                p.removeprefix(module + "/"): plugin.read(p)
                for p in plugin.namelist()
                if p.startswith(module + "/") and not p.endswith("/")
            }
        if actual != sources:
            raise ValueError("Plugin distribution differs from committed resources: " + identity)
        plugin_assets.update({f"plugins/{identity}/{module}/{p}": b for p, b in actual.items()})
    bundled = expected | {"plugin_catalog/" + k: v for k, v in catalog.items()}
    if {p.removeprefix(prefix): b for p, b in members.items() if p.startswith(prefix)} != bundled:
        raise ValueError("Wheel runtime resources disagree with committed product inputs")
    if {
        p: b
        for p, b in members.items()
        if p.startswith(("setup_system/", "setup_system_sdk/")) and not p.startswith(prefix)
    } != modules:
        raise ValueError("Wheel modules differ from source")
    with tarfile.open(sdist, "r:gz") as source:
        base = sdist.name.removesuffix(".tar.gz") + "/"
        for name, blob in (
            bundled | plugin_source | {"src/" + p: b for p, b in modules.items()}
        ).items():
            item = source.extractfile(base + name)
            if item is None or item.read() != blob:
                raise ValueError("Source distribution payload differs: " + name)
    deb_data = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", deb], timeout=90)
    package = {
        (GUIDES.parent if p.startswith("docs/") else INSTALLED) / p: b
        for p, b in (bundled | plugin_assets).items()
    }
    package.update({PYTHON_PACKAGE.parent / p: b for p, b in modules.items()})
    package.update(
        {
            INSTALLED / "sdk" / p.removeprefix("setup_system_sdk/"): b
            for p, b in modules.items()
            if p.startswith("setup_system_sdk/") and p.endswith((".mk", ".yml"))
        }
    )
    with tarfile.open(fileobj=io.BytesIO(deb_data)) as archive:
        actual_files = {}
        for entry in archive:
            member_path = Path("/") / entry.name
            if entry.isdir() or not any(
                member_path.is_relative_to(location)
                for location in (
                    INSTALLED,
                    GUIDES,
                    PYTHON_PACKAGE,
                    PYTHON_PACKAGE.parent / "setup_system_sdk",
                )
            ):
                continue
            if (
                not entry.isfile()
                or member_path in actual_files
                or entry.mode & 0o022
                or entry.uid
                or entry.gid
            ):
                raise ValueError("Invalid Debian resource: " + str(member_path))
            item = archive.extractfile(entry)
            if item is None:
                raise ValueError("Missing Debian member")
            actual_files[member_path] = item.read()
        if actual_files != package:
            raise ValueError("Debian resources disagree with source or plugin wheels")


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
        smoke = [
            str(executable),
            "-I",
            "-B",
            str(root / "docker/verify-installed.py"),
            "--wheel",
            str(wheels[0]),
        ]
        subprocess.run(smoke, cwd=stage, env=environment, timeout=120, check=True)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--reinstall",
                "--no-deps",
                "--no-index",
                "--python",
                str(executable),
                str(wheels[0]),
            ],
            cwd=stage,
            env=environment,
            timeout=60,
            check=True,
        )
        subprocess.run(smoke, cwd=stage, env=environment, timeout=120, check=True)
        subprocess.run(
            ["uv", "pip", "uninstall", "--python", str(executable), PACKAGE],
            cwd=stage,
            env=environment,
            timeout=60,
            check=True,
        )
        if os.path.lexists(executable.parent / "setup-system"):
            raise ValueError("The removed wheel still exposes its public command")
        subprocess.run(
            [
                str(executable),
                "-I",
                "-c",
                "from importlib.util import find_spec; import sys; "
                "names = ('setup_system', 'setup_system_sdk'); "
                "sys.exit(any(find_spec(name) is not None for name in names))",
            ],
            cwd=stage,
            env=environment,
            timeout=15,
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
