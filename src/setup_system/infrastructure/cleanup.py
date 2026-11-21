"""Read-only APT removal preview and privileged re-resolution of exact removal closure."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.domain.cleanup import CleanupCandidate, CleanupPlan, validate_removals
from setup_system.domain.system_commands import CleanupOptions
from setup_system.infrastructure.catalog_repository import load_cleanup


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
    packages: tuple[str, ...],
    *,
    editor_verified: bool = False,
    mode: str = "remove",
    catalog: Mapping[str, CleanupCandidate] | None = None,
) -> CleanupPlan:
    if catalog is None:
        catalog = load_cleanup()
    selected = frozenset(packages)
    if mode not in {"remove", "purge", "autoremove"}:
        raise ValueError("Unknown cleanup action")
    if not selected or not selected <= catalog.keys():
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
            requested=frozenset(),
            removed=frozenset(),
            essential=frozenset(essential),
            simulation="Selected candidates are already absent",
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
        selected,
        frozenset(removed),
        frozenset(essential),
        editor_verified=editor_verified,
        catalog=catalog,
    )
    return CleanupPlan(
        requested=selected,
        removed=frozenset(removed),
        essential=frozenset(essential),
        simulation=output,
    )


def inspect_cleanup(
    packages: tuple[str, ...], mode: str, *, root: Path | None = None
) -> CleanupPlan:
    from platformdirs import user_config_path

    catalog = load_cleanup(root)
    if not set(packages) <= catalog.keys():
        raise ValueError("Unknown cleanup selection")
    verified = False
    if any(catalog[p].requires_verified_editor for p in packages):
        marker = user_config_path("setup-system") / "editor.json"
        from setup_system.infrastructure.user_storage import safe_file

        safe_file(marker, os.getuid())
        if marker.is_file() and marker.stat().st_size < 4096:
            saved = json.loads(marker.read_text())
            verified = (
                saved.get("EDITOR") == "nvim"
                and saved.get("VISUAL") == "nvim"
                and administrative_editor(set(packages))
            )
    return preview(packages, editor_verified=verified, mode=mode, catalog=catalog)


def apply_cleanup(settings: CleanupOptions, *, root: Path | None = None) -> None:
    from setup_system.bootstrap.source import caller
    from setup_system.infrastructure.admin_editor import editor_ready

    account = caller()
    mode, packages = settings.mode, settings.packages
    config = settings.xdg.config if settings.xdg else Path(account.pw_dir) / ".config"
    editor_verified = False
    catalog = load_cleanup(root)
    if not set(packages) <= catalog.keys():
        raise ValueError("Unknown cleanup package")
    if any(catalog[p].requires_verified_editor for p in packages):
        editor_verified = editor_ready(account.pw_name, config) and administrative_editor(
            set(packages)
        )
    plan = preview(tuple(packages), editor_verified=editor_verified, mode=mode, catalog=catalog)
    if settings.closure != tuple(sorted(plan.removed)):
        raise ValueError("Removal closure changed after confirmation")
    if os.geteuid() != 0:
        raise ValueError("Cleanup apply requires the privileged executor")
    if not plan.requested:
        return
    from setup_system.infrastructure import processes, system_files

    receipt = Path("/var/lib/setup-system/cleanup.json")
    record = {
        "schema_version": 1,
        "mode": mode,
        "packages": sorted(plan.requested),
        "status": "running",
    }
    system_files.write_system(receipt, json.dumps(record).encode())
    try:
        # dpkg never expands the requested removal set. A new dependency makes it fail.
        # APT has already resolved and validated the complete closure immediately above.
        processes.run_command(
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
        if preview(
            tuple(plan.removed), editor_verified=editor_verified, mode=mode, catalog=catalog
        ).requested:
            raise RuntimeError("Package database readback disagrees with the approved cleanup")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        record["status"] = "partial/failed"
        system_files.write_system(receipt, json.dumps(record).encode())
        raise PartialApplyError(error_text(exc)) from exc
    record["status"] = "complete"
    system_files.write_system(receipt, json.dumps(record).encode())


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
