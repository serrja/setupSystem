"""Open the packaged Make target from an installed Python distribution."""

import os
import sys
from pathlib import Path

from setup_system.runner import _environment, resource_root


def main() -> None:
    if sys.argv[1:] in (["--help"], ["-h"]):
        print("setup-system opens the Textual workstation configurator.")
        return
    if sys.argv[1:]:
        print("Usage: setup-system", file=sys.stderr)
        raise SystemExit(2)
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        print("Run setup-system as a normal user", file=sys.stderr)
        raise SystemExit(2)
    root = resource_root()
    env = _environment()
    env["PATH"] = str(Path(sys.executable).parent) + ":/usr/bin:/bin"
    os.chdir(root)
    os.execve("/usr/bin/make", ["make", "-rR", "-j1", "-f", str(root / "Makefile"), "run"], env)
