"""Unprivileged managed configuration phase, executed by the installed locked runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(root: Path) -> int:
    if os.geteuid() == 0:
        print("User configuration cannot run as root.", file=sys.stderr)
        return 2
    from setup_system.application.workstation import apply

    try:
        backup = apply(root, "hyprland")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FAILED user configuration: {exc}", file=sys.stderr)
        return 5
    print(f"User configuration complete. Backup/recovery ID: {backup.name}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
