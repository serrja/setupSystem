"""Conflict-aware user writes with a local, unprivileged Ansible Runner adapter."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from setup_system.infrastructure.config_store import safe_file
from setup_system.infrastructure.theme import RESOURCE_ROOT
from setup_system.storage import atomic_write, new_backup, state_root


def apply_files(desired: dict[Path, bytes]) -> Path:
    if os.geteuid() == 0:
        raise ValueError("User configuration and Ansible cannot run as root")
    receipt = state_root() / "workstation.json"
    safe_file(receipt, os.getuid())
    previous: dict[str, str] = json.loads(receipt.read_text()) if receipt.exists() else {}
    changed: dict[Path, bytes] = {}
    home = Path.home().resolve()
    for path, content in desired.items():
        if not path.is_relative_to(home):
            raise ValueError("User configuration escaped HOME")
        safe_file(path, os.getuid())
        if path.exists():
            current = path.read_bytes()
            if current == content:
                continue
            if previous.get(str(path)) != hashlib.sha256(current).hexdigest():
                raise ValueError("Unmanaged or edited configuration requires review: " + str(path))
        changed[path] = content
    if not changed:
        return receipt.parent
    backup = new_backup([path for path in (*changed, receipt) if path.exists()])
    atomic_write(
        backup / "operation.json",
        json.dumps(
            {
                "status": "running",
                "created": [str(p) for p in changed if not p.exists()],
                "after": {str(p): hashlib.sha256(data).hexdigest() for p, data in changed.items()},
            }
        ).encode(),
    )
    import ansible_runner  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory(prefix="setup-system-ansible-") as temporary:
        stage = Path(temporary)
        cfg = stage / "ansible.cfg"
        cfg.write_text("[defaults]\nhost_key_checking=True\nretry_files_enabled=False\n")
        entries = []
        for index, (path, content) in enumerate(changed.items()):
            path.parent.mkdir(parents=True, exist_ok=True)
            source = stage / str(index)
            source.write_bytes(content)
            entries.append(
                {
                    "source": str(source),
                    "destination": str(path),
                    "mode": "0700" if path.name == "start-hyprland" else "0644",
                }
            )
        playbook: list[dict[str, Any]] = [
            {
                "name": "Apply selected user files",
                "hosts": "localhost",
                "gather_facts": False,
                "connection": "local",
                "become": False,
                "roles": [{"role": str(RESOURCE_ROOT / "roles/user_files")}],
            }
        ]
        result = ansible_runner.run(
            private_data_dir=str(stage),
            playbook=playbook,
            inventory="localhost,",
            extravars={
                "user_files_managed_files": entries,
                "ansible_python_interpreter": sys.executable,
            },
            envvars={
                "ANSIBLE_CONFIG": str(cfg),
                "ANSIBLE_HOME": str(stage / "ansible"),
                "ANSIBLE_LOCAL_TEMP": str(stage / "local"),
                "ANSIBLE_REMOTE_TEMP": str(stage / "remote"),
            },
            quiet=True,
            timeout=300,
        )
        # Record each verified completed write even after a partial runner failure.
        completed = {}
        for path, content in changed.items():
            safe_file(path, os.getuid())
            if path.is_file() and path.read_bytes() == content:
                completed[str(path)] = hashlib.sha256(content).hexdigest()
        previous.update(completed)
        atomic_write(receipt, (json.dumps(previous, indent=2) + "\n").encode())
        atomic_write(
            backup / "operation.json",
            json.dumps(
                {
                    "status": "complete" if result.rc == 0 else "partial",
                    "completed": completed,
                    "after": {
                        str(p): hashlib.sha256(data).hexdigest() for p, data in changed.items()
                    },
                }
            ).encode(),
        )
        if result.rc != 0:
            raise RuntimeError(f"User configuration partially applied; backup: {backup}")
    for path, content in changed.items():
        if path.read_bytes() != content:
            raise RuntimeError("User configuration readback failed; backup: " + str(backup))
        previous[str(path)] = hashlib.sha256(content).hexdigest()
    atomic_write(receipt, (json.dumps(previous, indent=2) + "\n").encode())
    return backup
