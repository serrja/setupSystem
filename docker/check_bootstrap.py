"""Validate the read-only host plan and its explicit unsupported exit status."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
capabilities = tomllib.loads((root / "manifests/platforms.toml").read_text())
profile = capabilities["hyprland"]
release = platform.freedesktop_os_release()
machine = platform.machine()
machine = capabilities["architectures"].get(machine, machine)
supported = (
    release.get("ID") == profile["distribution"]
    and release.get("VERSION_ID") == profile["version"]
    and release.get("VERSION_CODENAME") == profile["codename"]
    and machine in profile["architectures"]
)
expected = (2 if os.geteuid() == 0 and not os.environ.get("SUDO_USER") else 0) if supported else 4
result = subprocess.run([sys.executable, "bootstrap.py", "plan"], check=False, timeout=30)
if result.returncode != expected:
    raise SystemExit(f"Read-only plan returned {result.returncode}; expected {expected}")
