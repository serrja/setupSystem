"""Invoke only the fixed stdlib release executor, with data separated from code."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from setup_system.infrastructure import trusted_bootstrap


class PrivilegedExecutor:
    def __init__(self, release: str, source_sha: str) -> None:
        self.release, self.source_sha = release, source_sha

    def run(self, action: str, settings: dict[str, object]) -> None:
        if os.geteuid() == 0:
            raise ValueError("The UI must remain unprivileged")
        if action not in {"base", "workspace_packages", "cleanup"}:
            raise ValueError("Unexpected privileged action")
        program = Path(trusted_bootstrap.__file__).read_text()
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
            subprocess.run(
                ["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-B", "-S", "-c", program],
                input=payload,
                capture_output=True,
                text=True,
                check=True,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
        except subprocess.CalledProcessError as exc:
            from setup_system.diagnostics import error_text

            reason = error_text(exc.stderr or "Privilege authorization or system stage failed")
            raise RuntimeError(reason) from None
