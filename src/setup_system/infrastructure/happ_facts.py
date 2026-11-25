"""Observe the reviewed vendor unit and Debian package before an explicit Happ action."""

import re
import subprocess
from pathlib import Path

from pydantic import JsonValue

from setup_system.domain.observations import HappBefore, HappUnit
from setup_system.infrastructure.system_paths import protected

UNIT_PATHS = (
    Path("/etc/systemd/system/happd.service"),
    Path("/run/systemd/system/happd.service"),
    Path("/usr/lib/systemd/system/happd.service"),
)


def installed_version() -> str | None:
    result = subprocess.run(
        ["/usr/bin/dpkg-query", "-W", "-f=${Status}|${Version}", "happ"],
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if (
        result.returncode == 1
        and not result.stdout
        and "no packages found matching happ" in result.stderr.lower()
    ):
        return None
    result.check_returncode()
    status, separator, version = result.stdout.strip().partition("|")
    if not separator or not re.fullmatch(r"[0-9A-Za-z.+:~-]+", version):
        raise ValueError("Review the installed Happ package metadata")
    if status in {"deinstall ok config-files", "purge ok not-installed"}:
        return None
    if status not in {"install ok installed", "hold ok installed"}:
        raise ValueError("Repair the partial Happ package before applying this plan")
    return version


def unit_info() -> dict[str, str]:
    for path in UNIT_PATHS:
        if path.is_symlink():
            raise ValueError("Happ has an administrator mask or unit link requiring review")
        protected(path, directory=False)
    args = [
        "/usr/bin/systemctl",
        "--system",
        "--no-ask-password",
        "--no-pager",
        "show",
        "happd.service",
        "--property=LoadState,ActiveState,FragmentPath",
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    facts = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if not (
        facts.get("LoadState") in {"loaded", "not-found"}
        and facts.get("ActiveState") in {"active", "inactive"}
        and result.returncode in {0, 4}
    ):
        raise ValueError("Cannot verify the installed Happ service with the system manager")
    fragment = facts.get("FragmentPath", "")
    if (fragment and Path(fragment) not in UNIT_PATHS) or (
        facts["LoadState"] == "loaded" and not fragment
    ):
        raise ValueError("Unexpected Happ systemd unit")
    return facts


def require_idle(old: str | None, new: str, unit: dict[str, str]) -> None:
    if old is None or old == new:
        return
    if unit["ActiveState"] == "active":
        raise ValueError("Disconnect Happ and stop its active service before updating")
    for name in ("Happ", "happ"):
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", name], stdout=subprocess.DEVNULL, timeout=10
        )
        if result.returncode != 1:
            raise ValueError("Close Happ and disconnect the active session before updating")


def verify_before(confirmed: JsonValue, new_version: str) -> None:
    """Recheck the actual installed version and systemd state before dpkg runs."""
    unit = unit_info()
    installed = installed_version()
    if HappBefore.model_validate(confirmed) != HappBefore(
        unit=HappUnit.model_validate(unit), installed=installed
    ):
        raise ValueError("Happ package or service changed after confirmation")
    require_idle(installed, new_version, unit)
