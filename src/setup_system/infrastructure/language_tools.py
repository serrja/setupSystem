"""Install only a selected, hash-locked language tool closure into a user environment."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from platformdirs import user_data_path

from setup_system.infrastructure.config_store import safe_file
from setup_system.infrastructure.theme import RESOURCE_ROOT
from setup_system.runtime import managed_uv
from setup_system.storage import atomic_write


def install_python_tools() -> None:
    if os.geteuid() == 0:
        raise ValueError("Language tools cannot be installed as root")
    from setup_system.planning import detect_platform

    capability = detect_platform()
    if not capability.implemented:
        raise ValueError(capability.reason)
    arch = capability.architecture
    uv = managed_uv(("debian", arch))
    requirements = RESOURCE_ROOT / "catalogs/python_tools.requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    environment = user_data_path("setup-system") / "language-tools/python"
    home = Path.home()
    if not environment.is_relative_to(home) or any(
        p.is_symlink() for p in (environment, *environment.parents)
    ):
        raise ValueError("Unsafe language-tool environment")
    receipt = environment / "setup-system.json"
    safe_file(receipt, os.getuid())
    if environment.exists():
        if (
            not receipt.is_file()
            or json.loads(receipt.read_text()).get("requirements_sha256") != digest
        ):
            raise ValueError("Existing language environment requires explicit review")
    else:
        subprocess.run(
            [str(uv), "venv", "--python", sys.executable, str(environment)], check=True, timeout=120
        )
        atomic_write(
            receipt, json.dumps({"requirements_sha256": digest, "status": "prepared"}).encode()
        )
    links = {
        name: home / ".local/bin" / name
        for name in ("ruff", "basedpyright", "basedpyright-langserver")
    }
    for name, link in links.items():
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.resolve() != environment / "bin" / name:
                raise ValueError("Existing language command is preserved for review: " + name)
    subprocess.run(
        [
            str(uv),
            "pip",
            "sync",
            "--require-hashes",
            "--python",
            str(environment / "bin/python"),
            str(requirements),
        ],
        check=True,
        timeout=900,
    )
    subprocess.run(
        [
            str(environment / "bin/python"),
            "-I",
            "-c",
            "from importlib.metadata import version; assert version('ruff') == '0.17.1'; "
            "assert version('basedpyright') == '1.40.2'",
        ],
        check=True,
        timeout=15,
    )
    for name, link in links.items():
        if not (environment / "bin" / name).is_file():
            raise RuntimeError("Language command is missing after install: " + name)
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.is_symlink():
            link.symlink_to(environment / "bin" / name)
    atomic_write(
        receipt, json.dumps({"requirements_sha256": digest, "status": "complete"}).encode()
    )
