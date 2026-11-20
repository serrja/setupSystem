"""Read-only APT removal preview and privileged re-resolution of exact removal closure."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from setup_system.domain.cleanup import CLEANUP_CANDIDATES, validate_removals


@dataclass(frozen=True)
class CleanupPlan:
    requested: frozenset[str]
    removed: frozenset[str]
    essential: frozenset[str]
    simulation: str


def _run(arguments: list[str]) -> str:
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
    ).stdout


def preview(packages: tuple[str, ...], *, editor_verified: bool = False) -> CleanupPlan:
    selected = frozenset(packages)
    if not selected or not selected <= CLEANUP_CANDIDATES:
        raise ValueError("Only explicit cleanup candidates are accepted")
    data = _run(
        [
            "/usr/bin/dpkg-query",
            "-W",
            "-f=${binary:Package}\t${db:Status-Status}\t${Essential}\t${Protected}\\n",
        ]
    )
    installed: set[str] = set()
    essential: set[str] = set()
    for line in data.splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError("Unrecognized dpkg package status")
        name = fields[0].split(":")[0]
        if fields[1] == "installed":
            installed.add(name)
        if "yes" in fields[2:]:
            essential.add(name)
    selected = selected & installed
    if not selected:
        return CleanupPlan(
            frozenset(), frozenset(), frozenset(essential), "Selected candidates are already absent"
        )
    output = _run(["/usr/bin/apt-get", "--simulate", "remove", "--", *sorted(selected)])
    removed: set[str] = set()
    for line in output.splitlines():
        if line.startswith(("Remv ", "Purg ")):
            match = re.fullmatch(
                r"(?:Remv|Purg) ([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9]+)?(?: \[[^]]+\])?", line
            )
            if not match:
                raise ValueError("Unrecognized APT removal record")
            removed.add(match[1])
        elif line.startswith("Inst "):
            raise ValueError("Cleanup unexpectedly installs or upgrades dependencies")
    validate_removals(
        selected, frozenset(removed), frozenset(essential), editor_verified=editor_verified
    )
    return CleanupPlan(selected, frozenset(removed), frozenset(essential), output)


def apply_cleanup(settings: dict[str, object]) -> None:
    from setup_system.infrastructure.privileged_bootstrap_helpers import editor_ready
    from setup_system.infrastructure.trusted_bootstrap import caller

    account = caller()
    if settings.get("mode") != "remove":
        raise ValueError("Purge and autoremove are separate unsupported operations")
    packages = settings.get("packages")
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        raise ValueError("Invalid cleanup selection")
    xdg = settings.get("xdg", {})
    if not isinstance(xdg, dict):
        raise ValueError("Invalid XDG settings")
    config = Path(xdg.get("config", str(Path(account.pw_dir) / ".config")))
    editor_verified = False
    if {"nano", "vim-tiny"} & set(packages):
        editor_verified = editor_ready(account.pw_name, config)
    plan = preview(tuple(packages), editor_verified=editor_verified)
    if settings.get("closure") != sorted(plan.removed):
        raise ValueError("Removal closure changed after confirmation")
    if os.geteuid() != 0:
        raise ValueError("Cleanup apply requires the privileged executor")
    if not plan.requested:
        return
    from setup_system import installation as system

    receipt = Path("/var/lib/setup-system/cleanup.json")
    record = {
        "schema_version": 1,
        "mode": "remove",
        "packages": sorted(plan.requested),
        "status": "running",
    }
    system._write_system(receipt, json.dumps(record).encode())
    try:
        # dpkg never expands the requested removal set. A new dependency makes it fail.
        # APT has already resolved and validated the complete closure immediately above.
        system.run_command(
            ["/usr/bin/dpkg", "--remove", "--", *sorted(plan.requested)],
            check=True,
            capture_output=True,
            text=True,
            timeout=1800,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
                "DEBIAN_FRONTEND": "noninteractive",
            },
        )
    except (OSError, subprocess.SubprocessError):
        record["status"] = "partial/failed"
        system._write_system(receipt, json.dumps(record).encode())
        raise
    record["status"] = "complete"
    system._write_system(receipt, json.dumps(record).encode())


def cleanup_catalog() -> dict[str, dict[str, bool]]:
    path = Path(__file__).resolve().parents[1] / "resources/catalogs/optional_cleanup.toml"
    data = tomllib.loads(path.read_text())
    entries = data.get("candidates")
    if (
        data.get("schema_version") != 1
        or not isinstance(entries, dict)
        or set(entries) != CLEANUP_CANDIDATES
    ):
        raise ValueError("Cleanup catalog does not match the reviewed operation allowlist")
    result = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) - {"requires_verified_editor"}:
            raise ValueError("Invalid cleanup candidate metadata")
        required = entry.get("requires_verified_editor", False)
        if type(required) is not bool:
            raise ValueError("Invalid fallback editor requirement")
        result[name] = {"requires_verified_editor": required}
    return result
