"""Verify wheel identity/resources without executing host setup operations."""

import subprocess
import tomllib
from importlib.metadata import version
from pathlib import Path

import setup_system
from setup_system.application.project_layout import load_layout
from setup_system.application.templates import load_templates

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
