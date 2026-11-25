"""Observe the active display manager and its selected systemd unit."""

from __future__ import annotations

import os
from pathlib import Path

from .login_files import LoginFiles


def manager_enabled(store: LoginFiles) -> bool:
    paths = store.paths
    store.check_path(paths.display_manager, leaf_link=True)
    if paths.local_unit.exists() or paths.local_unit.is_symlink():
        raise ValueError("An administrator greetd unit override needs separate review")
    for override in paths.unit_dropin.parent.glob("*.conf"):
        if override != paths.unit_dropin:
            raise ValueError("An existing greetd service drop-in needs separate review")
    if not paths.display_manager.is_symlink():
        if paths.display_manager.exists():
            raise ValueError("Existing display manager is not a supported greetd alias")
        return False
    destination = os.readlink(paths.display_manager)
    target = Path(os.path.normpath(str(paths.display_manager.parent / destination)))
    if target != paths.vendor_unit:
        raise ValueError("A different display manager is configured; it will not be replaced")
    return True
