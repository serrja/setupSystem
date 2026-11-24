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
from setup_system.infrastructure.palette import palette
from setup_system.runner import resource_root

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
projects = tomllib.loads((resources / "config/workspace/projects.toml").read_text())["project"]
assert "pokerlost/projectRules" in {item["id"] for item in projects}
assert (resources / "ansible/base.yml").is_file() and (
    resources / "ansible/workspace.yml"
).is_file()
assert palette(resources)["green"] == "#a6e3a1"
subprocess.run(["setup-system", "--help"], check=True)
ui_probe = """import asyncio
from pathlib import Path
from setup_system.app import SetupApp
from setup_system.runner import MakeRunner, Target
from setup_system.file_tasks import restore
from setup_system.infrastructure.managed_files import apply_files
async def exercise():
    app=SetupApp()
    async with app.run_test(size=(110,45)) as pilot:
        assert app.section == "base"
        await pilot.press("ctrl+2")
        assert app.section == "workspace"
        await pilot.press("ctrl+1")
        assert app.section == "base"
asyncio.run(exercise())
home = Path.home()
baseline = {"schema": 1, "settings": {}, "expected": None}
facts = MakeRunner().run(Target.BASE_INSPECT, baseline)
assert facts["status"] in {"ready", "blocked"} and "architecture" in facts["observed"], facts
assert not any(home.iterdir()), "Installed Inspect modified the user HOME"
request = {"schema": 1, "settings": {"project": str(home)}, "expected": None}
result = MakeRunner().run(Target.PROJECT_CHECK, request)
assert result["status"] == "ready", result
target = home / "config with spaces/example.ini"
operation = apply_files({target:b"installed configuration"})
assert target.read_bytes() == b"installed configuration"
restore(operation.name, apply=True)
assert not target.exists()
"""
with tempfile.TemporaryDirectory(prefix="setup-system-ui-") as ui_home:
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", ui_probe],
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
