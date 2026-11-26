"""Build a sample workstation archive and verify its installed inputs in isolation."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from verify_native import verify


def main() -> None:
    plugin = Path(__file__).resolve().parents[1]
    root = plugin.parents[1]
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        dist = stage / "wheels"
        for name in ("setup-system", "setup-system-sdk", "setup-system-plugin-debian"):
            subprocess.run(
                ["uv", "build", "--package", name, "--wheel", "--out-dir", str(dist)],
                cwd=root,
                check=True,
            )
        (host,) = dist.glob("setup_system-*.whl")
        (sdk,) = dist.glob("setup_system_sdk-*.whl")
        (provider,) = dist.glob("setup_system_plugin_debian-*.whl")
        catalog = stage / "catalog"
        subprocess.run(
            [
                sys.executable,
                str(root / "docker/plugin_artifacts.py"),
                "--primary",
                str(provider),
                "--sdk",
                str(sdk),
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
                sys.executable,
                str(plugin / "packaging/build.py"),
                "--source",
                oid,
                "--version",
                "0~check",
                "--host-wheel",
                str(host),
                "--sdk-wheel",
                str(sdk),
                "--release",
                str(catalog / "debian.json"),
                "--output",
                str(output),
            ],
            check=True,
            env={**os.environ, "SOURCE_DATE_EPOCH": "1"},
        )
        (archive,) = output.glob("*.deb")
        verify(archive)


if __name__ == "__main__":
    main()
