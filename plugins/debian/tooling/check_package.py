"""Build a sample workstation archive and verify its installed inputs in isolation."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from verify_native import verify


def verify_locked_runtime(archive: Path, stage: Path) -> None:
    """Check the package-owned wheel closures with the bundled uv executable."""
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise ValueError("The package runtime check needs a supported build architecture")
    package = stage / "installed"
    subprocess.run(["dpkg-deb", "--extract", str(archive), str(package)], check=True)
    assets = package / "usr/lib/setup-system/runtime"
    bundled_uv = assets / "uv" / architecture / "uv"
    subprocess.run(["uv", "python", "install", "3.13"], check=True)
    interpreter = subprocess.check_output(["uv", "python", "find", "3.13"], text=True).strip()
    environment = {
        "HOME": str(stage),
        "PATH": "/usr/bin:/bin",
        "UV_NO_CONFIG": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_PROGRESS": "1",
    }
    for role, imports in (
        ("host", "import setup_system, setup_system_sdk, sqlalchemy, alembic"),
        (
            "native",
            "import ansible, pexpect; "
            "from setup_system_sdk.worker import load_backend; "
            "[load_backend(name) for name in "
            "('administration', 'debian', 'greeter', 'plymouth', 'regional', 'snap')]",
        ),
    ):
        target = stage / "runtimes" / role
        subprocess.run(
            [
                str(bundled_uv),
                "venv",
                "--no-project",
                "--no-python-downloads",
                "--offline",
                "--python",
                interpreter,
                str(target),
            ],
            check=True,
            env=environment,
        )
        subprocess.run(
            [
                str(bundled_uv),
                "pip",
                "sync",
                str(assets / f"{role}-{architecture}.txt"),
                "--python",
                str(target / "bin/python"),
                "--offline",
                "--no-index",
                "--no-build",
                "--require-hashes",
                "--no-cache",
                "--find-links",
                str(assets / "wheelhouse" / architecture),
            ],
            check=True,
            env=environment,
        )
        subprocess.run(
            [str(bundled_uv), "pip", "check", "--python", str(target / "bin/python"), "--offline"],
            check=True,
            env=environment,
        )
        subprocess.run(
            [str(target / "bin/python"), "-I", "-B", "-c", imports],
            check=True,
            env=environment,
        )
    catalog = package / "usr/lib/setup-system/plugin_catalog"
    for descriptor in sorted(catalog.glob("*.json")):
        releases = json.loads(descriptor.read_bytes())["releases"]
        # The catalog emits amd64 first and arm64 second, collapsing equal variants.
        selected = releases[0 if architecture == "amd64" else -1]
        plugin = selected["manifest"]["id"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin):
            raise ValueError("Invalid package plugin identity")
        wheel_paths = []
        for wheel in selected["wheels"]:
            path = catalog / wheel["name"]
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != wheel["sha256"]:
                raise ValueError("Package plugin wheel differs from its release descriptor")
            wheel_paths.append(str(path))
        target = stage / "plugin-runtime"
        subprocess.run(
            [
                str(bundled_uv),
                "venv",
                "--clear",
                "--no-project",
                "--no-python-downloads",
                "--offline",
                "--python",
                interpreter,
                str(target),
            ],
            check=True,
            env=environment,
        )
        subprocess.run(
            [
                str(bundled_uv),
                "pip",
                "install",
                "--offline",
                "--no-index",
                "--no-build",
                "--no-deps",
                "--python",
                str(target / "bin/python"),
                *wheel_paths,
            ],
            check=True,
            env=environment,
        )
        subprocess.run(
            [str(bundled_uv), "pip", "check", "--python", str(target / "bin/python"), "--offline"],
            check=True,
            env=environment,
        )
        subprocess.run(
            [
                str(target / "bin/python"),
                "-I",
                "-B",
                "-c",
                f"from setup_system_sdk.worker import load_backend; load_backend({plugin!r})",
            ],
            check=True,
            env=environment,
        )
        shutil.rmtree(target)


def main() -> None:
    plugin = Path(__file__).resolve().parents[1]
    root = plugin.parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        dist = stage / "wheels"
        subprocess.run(
            ["uv", "build", "--all-packages", "--wheel", "--out-dir", str(dist)],
            cwd=root,
            check=True,
        )
        (host,) = dist.glob("setup_system-*.whl")
        (sdk,) = dist.glob("setup_system_sdk-*.whl")
        catalog = stage / "catalog"
        subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "--package",
                "setup-system",
                "python",
                "-m",
                "plugins.debian.tooling.catalog",
                "--wheels",
                str(dist),
                "--host-wheel",
                str(host),
                "--output",
                str(catalog),
            ],
            cwd=root,
            check=True,
        )
        oid = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        output = stage / "archive"
        subprocess.run(
            [
                "make",
                "-f",
                "ci/package.mk",
                "package",
                f"HOST_WHEEL={host}",
                f"SDK_WHEEL={sdk}",
                "RELEASES=" + " ".join(str(path) for path in sorted(catalog.glob("*.json"))),
                f"RUNTIME_ASSETS={catalog / 'runtime'}",
                f"PACKAGE_DIR={output}",
                "VERSION=0~check",
            ],
            check=True,
            cwd=plugin,
            env={**os.environ, "SOURCE_DATE_EPOCH": "1"},
        )
        (archive,) = output.glob("*.deb")
        verify(archive)
        verify_locked_runtime(archive, stage)
        source = subprocess.check_output(
            ["dpkg-deb", "-f", str(archive), "X-Setup-System-Source"], text=True
        ).strip()
        if source != oid:
            raise ValueError("The archive source differs from the checked revision")


if __name__ == "__main__":
    main()
