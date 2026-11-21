"""The installed entrypoint opens Textual; private adapters are not a second user UI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from setup_system.resource_locator import ResourceLocator


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the setupSystem Textual application.")
    parser.add_argument(
        "--source", type=Path, default=ResourceLocator.for_root().root, help=argparse.SUPPRESS
    )
    parser.add_argument("--user", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Open setup-system as the normal desktop user; sudo is requested on Apply")
    from setup_system.infrastructure.identity import resolve_target

    target = resolve_target(arguments.user)
    if Path.home() != target.home:
        parser.error("HOME must match the current account's passwd entry")
    if arguments.user is not None:
        import pwd

        if pwd.getpwnam(arguments.user).pw_uid != os.getuid():
            parser.error("The selected user differs from the current account")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("An interactive terminal is required")
    from setup_system.composition import build_app

    raise SystemExit(build_app(arguments.source.resolve()).run() or 0)
