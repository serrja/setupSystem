"""Invoke only the fixed stdlib release executor, with data separated from code."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from setup_system.bootstrap import loader
from setup_system.domain.system_commands import COMMAND, PrivilegedCommand, ReleaseIdentity


class PrivilegedExecutor:
    def __init__(self, release: str, source_sha: str) -> None:
        self.release, self.source_sha = release, source_sha

    @classmethod
    def from_marker(cls, path: Path) -> PrivilegedExecutor | None:
        """Read the user's delivery hint. Root verifies the published tag independently."""
        from setup_system.infrastructure.user_storage import safe_file

        safe_file(path, os.getuid())
        if not path.exists():
            return None
        if path.stat().st_size > 8192:
            raise ValueError("Invalid release record size")
        record = ReleaseIdentity.model_validate_json(path.read_bytes())
        return cls(record.release, record.source_sha)

    def run(self, command: PrivilegedCommand) -> None:
        if os.geteuid() == 0:
            raise ValueError("The UI must remain unprivileged")
        checked = COMMAND.validate_python(command)
        identity = ReleaseIdentity(release=self.release, source_sha=self.source_sha)
        payload = json.dumps({**identity.model_dump(), **checked.model_dump(mode="json")})
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
                    loader.PROGRAM,
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
