"""Verify wheel identity/resources without executing host setup operations."""

import subprocess
import tomllib
from importlib.metadata import version
from pathlib import Path

import setup_system
from setup_system.application.project_layout import load_layout

expected = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
root = Path(setup_system.__file__).parent
assert "site-packages" in root.parts
assert version("setup-system") == expected
assert any(project.id == "pokerlost/platformInfra" for project in load_layout(root).projects)
subprocess.run(["setup-system", "--help"], check=True)
