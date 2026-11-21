"""Inspect and publish the reviewed Firefox enterprise policy without profile access."""

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from setup_system.infrastructure.system_files import validate_system_path
from setup_system.infrastructure.system_publication import (
    ObservedFile,
    observe_file,
    publish_file,
    save_backup,
)


@dataclass(frozen=True)
class PrivateDirectory:
    path: Path
    child: Path
    device: int
    inode: int


@dataclass(frozen=True)
class FirefoxPolicy:
    path: Path
    before: ObservedFile | None
    content: bytes
    private: tuple[PrivateDirectory, ...]


def inspect(path: Path = Path("/etc/firefox/policies/policies.json")) -> FirefoxPolicy:
    extension_id = "uBlock0@raymondhill.net"
    install_url = f"https://addons.mozilla.org/firefox/downloads/latest/{extension_id}/latest.xpi"
    desired = {
        "policies": {
            "EnableTrackingProtection": {
                "Value": True,
                "Locked": False,
                "Cryptomining": True,
                "Fingerprinting": True,
            },
            "Preferences": {
                "browser.contentblocking.category": {"Value": "strict", "Status": "default"},
                "dom.security.https_only_mode": {"Value": True, "Status": "default"},
            },
            "ExtensionSettings": {
                extension_id: {
                    "installation_mode": "normal_installed",
                    "install_url": install_url,
                },
            },
        }
    }
    before = observe_file(path)
    raw = before.data if before else None
    content = (json.dumps(desired, indent=2) + "\n").encode()
    legacy = content.replace(
        b"/" + extension_id.encode() + b"/latest.xpi", b"/ublock-origin/latest.xpi"
    )
    if raw is not None and raw not in {legacy, content}:
        raise ValueError("Existing enterprise Firefox policies require manual review")
    private = []
    for directory, child in ((path.parent.parent, path.parent), (path.parent, path)):
        if directory.exists() and not directory.stat().st_mode & stat.S_IXOTH:
            info = directory.lstat()
            if (
                raw is None
                or stat.S_IMODE(info.st_mode) != 0o700
                or set(directory.iterdir()) != {child}
            ):
                raise ValueError(
                    "Private Firefox policy directory has unknown contents; review permissions"
                )
            private.append(PrivateDirectory(directory, child, info.st_dev, info.st_ino))
    return FirefoxPolicy(path, before, content, tuple(private))


def _expose(directory: PrivateDirectory, retained: Path | None) -> None:
    validate_system_path(directory.path, is_directory=True)
    descriptor = os.open(directory.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        names = {directory.child.name}
        if retained is not None and retained.parent == directory.path:
            names.add(retained.name)
        if (
            (info.st_dev, info.st_ino) != (directory.device, directory.inode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or set(os.listdir(descriptor)) != names
        ):
            raise ValueError(
                "Private Firefox directory changed during migration; review permissions"
            )
        os.fchmod(descriptor, 0o711)
        if set(os.listdir(descriptor)) != names:
            if stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o711:
                os.fchmod(descriptor, 0o700)
            raise ValueError(
                "Private Firefox directory changed during migration; review permissions"
            )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply(
    plan: FirefoxPolicy, *, backups: Path = Path("/var/lib/setup-system/settings-backups")
) -> None:
    if inspect(plan.path) != plan:
        raise ValueError("Firefox policy changed after inspection")
    if plan.before is not None and (
        plan.before.data != plan.content or plan.before.mode != 0o644 or plan.private
    ):
        save_backup(backups / (plan.before.digest + ".json"), plan.before)
    retained = publish_file(plan.path, plan.content, plan.before, mode=0o644, parent_mode=0o755)
    for directory in reversed(plan.private):
        current = observe_file(plan.path)
        if current is None or current.data != plan.content or current.mode != 0o644:
            raise ValueError("Firefox policy changed before opening its private directory")
        _expose(directory, retained)
