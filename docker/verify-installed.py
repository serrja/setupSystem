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
from setup_system.application.project_layout import load_layout
from setup_system.application.templates import load_templates

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
assert any(project.id == "pokerlost/platformInfra" for project in load_layout(root).projects)
subprocess.run(["setup-system", "--help"], check=True)

sources = load_templates(root)
assert set(sources) == {"python", "typer", "fastapi", "service"}
for alias in ("python", "typer", "fastapi"):
    metadata = sources[alias].metadata
    assert metadata.versions and metadata.recommended_version is not None
    assert metadata.recommended_version in {item.version for item in metadata.versions}
subprocess.run(["git", "--version"], check=True)
subprocess.run(["setup-system", "templates", "list"], check=True)
for alias in sources:
    subprocess.run(["setup-system", "templates", "versions", alias], check=True)
for command in ("create", "update", "cache"):
    subprocess.run(["setup-system", "templates", command, "--help"], check=True)

# Exercise the installed UI in a fresh interpreter: captured stdio must not break
# the real backend process. The missing source rejects plan before any host apply.
ui_probe = """import asyncio,sys
from pathlib import Path
from setup_system.application.installer import InstallRequest,Operation
from setup_system.tui.installer import InstallationApp
async def exercise():
 app=InstallationApp(Path(sys.argv[1]),InstallRequest(Operation.PLAN))
 async with app.run_test(size=(100,40)) as pilot:
  await pilot.click('#plan')
  await app.workers.wait_for_complete()
  await pilot.pause()
  assert app.last_code==4 and not app.busy,app.output_lines
asyncio.run(exercise())
"""
with tempfile.TemporaryDirectory(prefix="setup-system-ui-") as ui_home:
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", ui_probe, str(Path(ui_home) / "missing-source")],
        env={**os.environ, "HOME": ui_home},
        timeout=60,
        check=True,
    )
