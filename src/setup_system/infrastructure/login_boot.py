"""Observe the selected display manager and existing recoverable GRUB installation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from setup_system.infrastructure.login_files import LoginFiles


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


def grub_fingerprints(
    store: LoginFiles,
    observed: dict[str, str | None] | None = None,
    deferred: list[str] | None = None,
) -> tuple[str | None, str]:
    """Capture each readable GRUB input even when the boot image is root-only."""
    paths = store.paths
    evidence = {} if observed is None else observed
    inputs: list[tuple[str, str, int]] = []

    def mark_private(path: Path) -> None:
        if deferred is None or os.geteuid() == 0:
            raise PermissionError(f"GRUB input is unreadable: {path}")
        deferred.append(str(path))

    def inspect(path: Path, *, executable: bool = False) -> None:
        try:
            content = store.read(path)
            if content is None or (executable and not os.access(path, os.X_OK)):
                raise ValueError(f"Missing required GRUB input or executable: {path}")
            if (
                path.parent == paths.grub_defaults
                and path.name > paths.grub_dropin.name
                and b"GRUB_" in content
            ):
                raise ValueError("A later GRUB drop-in could override the requested boot policy")
            file_hash = hashlib.sha256(content).hexdigest()
            mode = stat.S_IMODE(path.stat().st_mode)
            inputs.append((str(path), file_hash, mode))
            evidence[str(path)] = hashlib.sha256(f"{mode}:{file_hash}".encode()).hexdigest()
        except PermissionError:
            mark_private(path)

    inspect(paths.grub_default)
    inspect(paths.grub_generator, executable=True)
    inspect(paths.grub_checker, executable=True)
    for directory in (paths.grub_scripts, paths.grub_defaults):
        try:
            store.check_path(directory)
            try:
                entries = tuple(directory.iterdir())
            except FileNotFoundError:
                if directory == paths.grub_scripts:
                    raise
                entries = ()
            selected = sorted(
                path
                for path in entries
                if (
                    path.name.endswith(".cfg")
                    if directory == paths.grub_defaults
                    else path.is_file() and path.stat().st_mode & 0o111
                )
            )
            names = json.dumps([path.name for path in selected]).encode()
            evidence[str(directory)] = hashlib.sha256(names).hexdigest()
            for path in selected:
                inspect(path)
        except PermissionError:
            mark_private(directory)
    try:
        digest = store.fingerprint(paths.grub_config, limit=16 * 1024 * 1024)
        if digest is None:
            raise ValueError("No installed GRUB configuration; no bootloader will be installed")
    except PermissionError:
        mark_private(paths.grub_config)
        digest = None
    encoded = json.dumps(sorted(inputs), separators=(",", ":")).encode()
    return digest, hashlib.sha256(encoded).hexdigest()
