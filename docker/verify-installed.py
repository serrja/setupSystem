"""Verify wheel identity/resources without executing host setup operations."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import Distribution, distribution, version
from importlib.resources import files
from pathlib import Path

import setup_system


def verify_origin(installed: Distribution, wheel: Path | None) -> None:
    origin = json.loads(installed.read_text("direct_url.json") or "{}")
    if origin.get("dir_info", {}).get("editable"):
        raise ValueError("Expected an independently installed release")
    if wheel is None:
        return
    wheel = wheel.resolve(strict=True)
    if origin.get("url") != wheel.as_uri():
        raise ValueError("Installed wheel origin differs")
    recorded_hash = origin.get("archive_info", {}).get("hashes", {}).get("sha256")
    if (
        recorded_hash is not None
        and recorded_hash != hashlib.sha256(wheel.read_bytes()).hexdigest()
    ):
        raise ValueError("Installed archive digest differs")
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = Path(member.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or any(p.endswith(".data") for p in name.parts)
            ):
                raise ValueError("Unexpected installed wheel path")
            if member.is_dir() or member.filename.endswith(".dist-info/RECORD"):
                continue
            target = Path(str(installed.locate_file(member.filename)))
            if (
                not target.is_file()
                or target.is_symlink()
                or target.read_bytes() != archive.read(member)
            ):
                raise ValueError("Installed resource differs from its release")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    verify_origin(distribution("setup-system"), args.wheel)
    root = Path(setup_system.__file__).parent
    assert "site-packages" in root.parts
    assert files("setup_system").joinpath("_migrations", "env.py").is_file()
    expected = version("setup-system")
    output = subprocess.check_output(
        [sys.executable, "-I", "-B", "-m", "setup_system", "--version"], text=True
    )
    if output.strip() != expected:
        raise ValueError("CLI version differs from the installed metadata")
    subprocess.run(["setup-system", "--help"], check=True)
    with tempfile.TemporaryDirectory(prefix="setup-system-schema-") as home:
        subprocess.run(
            [sys.executable, "-I", "-B", "-m", "setup_system", "migrate"],
            cwd=home,
            env={
                "HOME": home,
                "PATH": os.environ["PATH"],
                "XDG_STATE_HOME": str(Path(home) / "state"),
            },
            timeout=60,
            check=True,
        )


if __name__ == "__main__":
    main()
