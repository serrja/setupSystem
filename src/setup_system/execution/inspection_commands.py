"""Allow only the bounded read-only system queries used by clock and storage inspection."""

from __future__ import annotations

import errno
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from setup_system.execution.processes import MAX_OUTPUT, run_command

COMMAND_TIMEOUT = 10


TIME_PROVIDERS = ("chrony", "ntpsec", "openntpd", "systemd-timesyncd")


InspectionRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def safe_absolute(value: str) -> bool:
    return (
        Path(value).is_absolute()
        and ".." not in Path(value).parts
        and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)
    )


def _query_allowed(argv: list[str]) -> bool:
    if argv == [
        "/usr/bin/timedatectl",
        "show",
        "--all",
        "--property=Timezone,NTP,NTPSynchronized,LocalRTC,CanNTP",
    ]:
        return True
    if len(argv) == 6 and argv[:3] == ["/usr/bin/findmnt", "--json", "--target"]:
        return safe_absolute(argv[3]) and argv[4:] == ["--output", "SOURCE,FSTYPE"]
    if len(argv) == 8 and argv[:7] == [
        "/usr/bin/lsblk",
        "--json",
        "--inverse",
        "--tree",
        "--output",
        "NAME,TYPE,FSTYPE",
        "--",
    ]:
        return (
            safe_absolute(argv[7]) and re.fullmatch(r"/dev/[A-Za-z0-9_+./:-]+", argv[7]) is not None
        )
    if len(argv) == 3 and argv[:2] == ["/usr/bin/fscrypt", "status"]:
        return safe_absolute(argv[2])
    if len(argv) == 4 and argv[:3] == ["/usr/bin/lsattr", "-d", "--"]:
        return safe_absolute(argv[3])
    return (
        len(argv) == 4
        and argv[:3] == ["/usr/bin/dpkg-query", "--show", "--showformat=${Status}"]
        and argv[3] in TIME_PROVIDERS
    )


def run_inspection(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run only fixed read-only queries with no inherited proxy or account environment."""
    if not _query_allowed(argv):
        raise ValueError("Expected a fixed read-only inspection query")
    return run_command(
        argv,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=COMMAND_TIMEOUT,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
    )


def read_query(run: InspectionRunner, argv: list[str], *, missing_key: bool = False) -> str | None:
    try:
        result = run(argv)
    except OSError as exc:
        if missing_key and exc.errno == errno.ENOKEY:
            raise
        return None
    except subprocess.SubprocessError:
        return None
    if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > MAX_OUTPUT:
        return None
    return result.stdout
