"""Verify wheel identity/resources without executing host setup operations."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from importlib.metadata import distribution, version
from pathlib import Path

import setup_system
from setup_system.extensions import builtins
from setup_system.infrastructure.palette import palette
from setup_system.layout import resource_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    installed = distribution("setup-system")
    origin = json.loads(installed.read_text("direct_url.json") or "{}")
    assert origin.get("dir_info", {}).get("editable") is not True
    if args.wheel:
        wheel = args.wheel.resolve(strict=True)
        assert origin["url"] == wheel.as_uri()
        recorded_hash = origin.get("archive_info", {}).get("hashes", {}).get("sha256")
        if recorded_hash is not None:
            assert recorded_hash == hashlib.sha256(wheel.read_bytes()).hexdigest()
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                name = Path(member.filename)
                assert not name.is_absolute() and ".." not in name.parts
                assert not any(part.endswith(".data") for part in name.parts)
                if member.is_dir() or member.filename.endswith(".dist-info/RECORD"):
                    continue
                target = Path(str(installed.locate_file(member.filename)))
                assert target.is_file() and not target.is_symlink()
                assert (
                    hashlib.sha256(target.read_bytes()).digest()
                    == hashlib.sha256(archive.read(member)).digest()
                )
    expected = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    root = Path(setup_system.__file__).parent
    assert "site-packages" in root.parts
    assert version("setup-system") == expected
    resources = resource_root()
    assert resources == root / "bundle"
    entries = builtins(resources)
    if not entries or any(item.reason or not item.manifest or not item.release for item in entries):
        raise ValueError("An installed plugin lacks a valid release identity")
    projects = next(e for e in entries if e.manifest and e.manifest.id == "projects")
    assert projects.source and projects.manifest
    module = projects.manifest.entry_point.partition(":")[0]
    with zipfile.ZipFile(projects.source) as plugin:
        catalog = tomllib.loads(plugin.read(module + "/config/workspace/projects.toml").decode())
    assert "pokerlost/projectRules" in {item["id"] for item in catalog["project"]}
    source_palette = tomllib.loads((resources / "config/shared/palette.toml").read_text())
    assert palette(resources) == {
        key: value for key, value in source_palette.items() if key != "name"
    }
    subprocess.run(["setup-system", "--help"], check=True)
    with tempfile.TemporaryDirectory(prefix="setup-system-ui-") as ui_home:
        subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).with_name("installed_smoke.py"))],
            env={
                **os.environ,
                "HOME": ui_home,
                **{
                    f"XDG_{name.upper()}_HOME": str(Path(ui_home) / name)
                    for name in ("config", "state", "data", "cache")
                },
            },
            timeout=60,
            check=True,
        )


if __name__ == "__main__":
    main()
