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
from setup_system.application.templates import load_templates
from setup_system.infrastructure.palette import palette
from setup_system.infrastructure.project_layout import load_layout

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
assert sources
for source in sources.values():
    metadata = source.metadata
    assert metadata.versions and metadata.recommended_version is not None
    assert metadata.recommended_version in {item.version for item in metadata.versions}
subprocess.run(["git", "--version"], check=True)
# Installed resources and headless navigation are checked outside the source checkout.
assert palette()["green"] == "#a6e3a1"
ui_probe = """import asyncio,sys
from pathlib import Path
from setup_system.composition import build_app
from setup_system.domain.identity import TargetUser
import os
from textual.widgets import TabbedContent
async def exercise():
 app=build_app(Path(sys.argv[1]),identity=lambda _:TargetUser(
  "fixture",os.getuid(),os.getgid(),Path.home(),"/bin/sh"))
 async with app.run_test(size=(100,40)) as pilot:
  assert not app.busy
  await pilot.press('ctrl+2')
  assert app.query_one('#sections',TabbedContent).active=='workspace'
  await pilot.click('#plan')
  await app.workers.wait_for_complete()
  await pilot.pause()
  assert app.last_code==0 and not app.busy,app.output_lines
asyncio.run(exercise())

from setup_system.infrastructure.managed_files import apply_files
from setup_system.composition import restore
target = Path.home() / "config with spaces/example.ini"
operation = apply_files({target: b"installed wheel role\\n"})
assert target.read_bytes() == b"installed wheel role\\n"
restore(operation.name, apply=True)
assert not target.exists()
"""
with tempfile.TemporaryDirectory(prefix="setup-system-ui-") as ui_home:
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", ui_probe, str(root)],
        env={
            **os.environ,
            "HOME": ui_home,
            "XDG_CONFIG_HOME": str(Path(ui_home) / "config"),
            "XDG_STATE_HOME": str(Path(ui_home) / "state"),
            "XDG_DATA_HOME": str(Path(ui_home) / "data"),
            "XDG_CACHE_HOME": str(Path(ui_home) / "cache"),
        },
        timeout=60,
        check=True,
    )
