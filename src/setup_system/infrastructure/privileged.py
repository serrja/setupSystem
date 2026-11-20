"""Invoke only the fixed stdlib release executor, with data separated from code."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from setup_system.infrastructure import release_loader, trusted_bootstrap


class PrivilegedExecutor:
    def __init__(self, release: str, source_sha: str) -> None:
        self.release, self.source_sha = release, source_sha

    @classmethod
    def from_marker(cls, path: Path) -> PrivilegedExecutor | None:
        """Read the user's delivery hint. Root verifies the published tag independently."""
        from setup_system.infrastructure.config_store import safe_file

        safe_file(path, os.getuid())
        if not path.exists():
            return None
        if path.stat().st_size > 8192:
            raise ValueError("Invalid release record size")
        record = json.loads(path.read_bytes())
        if not isinstance(record, dict) or record.keys() != {"release", "source_sha"}:
            raise ValueError("Unknown delivery record")
        tag, sha = record["release"], record["source_sha"]
        if (
            not isinstance(tag, str)
            or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-dev\.[0-9]+)?", tag)
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", sha)
        ):
            raise ValueError("Invalid delivery identity")
        return cls(tag, sha)

    def run(self, action: str, settings: dict[str, object]) -> None:
        if os.geteuid() == 0:
            raise ValueError("The UI must remain unprivileged")
        if action not in trusted_bootstrap.SYSTEM_ACTIONS:
            raise ValueError("Unexpected privileged action")
        payload = json.dumps(
            {
                "release": self.release,
                "source_sha": self.source_sha,
                "action": action,
                "settings": settings,
            }
        )
        try:
            # The root adapter owns command deadlines. Never kill dpkg from the UI.
            result = subprocess.run(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "--",
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-S",
                    "-c",
                    release_loader.PROGRAM,
                ],
                input=payload,
                capture_output=True,
                text=True,
                check=True,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
            from setup_system.infrastructure.catalog_deb import record_reported_attempts

            record_reported_attempts(result.stdout)
        except subprocess.CalledProcessError as exc:
            from setup_system.infrastructure.catalog_deb import record_reported_attempts

            record_reported_attempts(exc.stdout or "")
            from setup_system.diagnostics import PartialApplyError, error_text

            reason = error_text(exc.stderr or "Privilege authorization or system stage failed")
            if exc.returncode == 6:
                raise PartialApplyError(reason) from None
            raise RuntimeError(reason) from None
