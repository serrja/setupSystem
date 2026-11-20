"""Read-only APT removal preview and privileged re-resolution of exact removal closure."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.domain.cleanup import cleanup_catalog as cleanup_catalog
from setup_system.domain.cleanup import validate_removals


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


def preview(
    packages: tuple[str, ...], *, editor_verified: bool = False, mode: str = "remove"
) -> CleanupPlan:
    selected = frozenset(packages)
    if mode not in {"remove", "purge", "autoremove"}:
        raise ValueError("Unknown cleanup action")
    if not selected or not selected <= cleanup_catalog().keys():
        raise ValueError("Only explicit cleanup candidates are accepted")
    data = _run(
        [
            "/usr/bin/dpkg-query",
            "-W",
            "-f=${binary:Package}\t${db:Status-Status}\t${db:Status-Want}\t${Essential}\t${Protected}\\n",
        ]
    )
    installed: set[str] = set()
    essential: set[str] = set()
    for line in data.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            raise ValueError("Unrecognized dpkg package status")
        name = fields[0].split(":")[0]
        if name in selected and name in installed:
            raise ValueError("Multi-architecture package selection requires explicit review")
        if fields[1] == "installed" or (mode == "purge" and fields[1] == "config-files"):
            installed.add(name)
        if "yes" in fields[3:] or fields[2] == "hold":
            essential.add(name)
    selected = selected & installed
    if not selected:
        return CleanupPlan(
            frozenset(), frozenset(), frozenset(essential), "Selected candidates are already absent"
        )
    arguments = [] if mode == "autoremove" else ["--", *sorted(selected)]
    output = _run(["/usr/bin/apt-get", "--simulate", mode, *arguments])
    removed: set[str] = set()
    for line in output.splitlines():
        if line.startswith(("Remv ", "Purg ")):
            match = re.fullmatch(
                r"(?:Remv|Purg) ([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9]+)?(?: \[[^]]+\])?", line
            )
            if not match:
                raise ValueError("Unrecognized APT removal record")
            removed.add(match[1])
        elif line.startswith(("Inst ", "Conf ")):
            raise ValueError("Cleanup unexpectedly installs or upgrades dependencies")
    validate_removals(
        selected, frozenset(removed), frozenset(essential), editor_verified=editor_verified
    )
    return CleanupPlan(selected, frozenset(removed), frozenset(essential), output)


def inspect_cleanup(packages: tuple[str, ...], mode: str) -> CleanupPlan:
    from platformdirs import user_config_path

    catalog = cleanup_catalog()
    if not set(packages) <= catalog.keys():
        raise ValueError("Unknown cleanup selection")
    verified = False
    if any(catalog[p]["requires_verified_editor"] for p in packages):
        marker = user_config_path("setup-system") / "editor.json"
        from setup_system.infrastructure.config_store import safe_file

        safe_file(marker, os.getuid())
        if marker.is_file() and marker.stat().st_size < 4096:
            saved = json.loads(marker.read_text())
            verified = (
                saved.get("EDITOR") == "nvim"
                and saved.get("VISUAL") == "nvim"
                and administrative_editor(set(packages))
            )
    return preview(packages, editor_verified=verified, mode=mode)


def apply_cleanup(settings: dict[str, object]) -> None:
    from setup_system.infrastructure.privileged_bootstrap_helpers import editor_ready
    from setup_system.infrastructure.trusted_bootstrap import caller

    account = caller()
    mode = settings.get("mode")
    if not isinstance(mode, str) or mode not in {"remove", "purge", "autoremove"}:
        raise ValueError("Invalid cleanup action")
    packages = settings.get("packages")
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        raise ValueError("Invalid cleanup selection")
    xdg = settings.get("xdg", {})
    if not isinstance(xdg, dict):
        raise ValueError("Invalid XDG settings")
    config = Path(xdg.get("config", str(Path(account.pw_dir) / ".config")))
    editor_verified = False
    catalog = cleanup_catalog()
    if not set(packages) <= catalog.keys():
        raise ValueError("Unknown cleanup package")
    if any(catalog[p]["requires_verified_editor"] for p in packages):
        editor_verified = editor_ready(account.pw_name, config) and administrative_editor(
            set(packages)
        )
    plan = preview(tuple(packages), editor_verified=editor_verified, mode=mode)
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
        "mode": mode,
        "packages": sorted(plan.requested),
        "status": "running",
    }
    system._write_system(receipt, json.dumps(record).encode())
    try:
        # dpkg never expands the requested removal set. A new dependency makes it fail.
        # APT has already resolved and validated the complete closure immediately above.
        system.run_command(
            [
                "/usr/bin/dpkg",
                "--purge" if mode == "purge" else "--remove",
                "--",
                *sorted(plan.removed),
            ],
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
        if preview(tuple(plan.removed), editor_verified=editor_verified, mode=mode).requested:
            raise RuntimeError("Package database readback disagrees with the approved cleanup")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        record["status"] = "partial/failed"
        system._write_system(receipt, json.dumps(record).encode())
        raise PartialApplyError(error_text(exc)) from exc
    record["status"] = "complete"
    system._write_system(receipt, json.dumps(record).encode())


def administrative_editor(removing: set[str]) -> bool:
    """Require an executable system editor whose Debian package will survive removal."""
    for binary in ("/usr/bin/vi", "/usr/bin/nano", "/usr/bin/ed", "/usr/bin/nvim"):
        path = Path(binary)
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        try:
            owner = _run(["/usr/bin/dpkg-query", "-S", str(path.resolve(strict=True))])
        except (OSError, subprocess.SubprocessError):
            continue
        package, separator, owned_path = owner.strip().partition(": ")
        if (
            separator
            and owned_path == str(path.resolve())
            and package.split(":")[0] not in removing
        ):
            return True
    return False
