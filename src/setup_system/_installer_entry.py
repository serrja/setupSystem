"""Private source-only handoff before the locked runtime exists; no public CLI."""

from __future__ import annotations

import sys
from pathlib import Path

MINIMUM_PYTHON = (3, 11)


if __name__ == "__main__":
    if sys.version_info < MINIMUM_PYTHON:
        raise SystemExit("System Python 3.11+ is required; use sudo bash ./setup.sh.")
    sys.dont_write_bytecode = True
    root = Path(__file__).resolve().parents[2]
    # This reviewed source bundle is not installed yet. Installed CLI imports stay native.
    sys.path.insert(0, str(root / "src"))
    from setup_system.entrypoint import main

    raise SystemExit(main(root, sys.argv[1:]))
