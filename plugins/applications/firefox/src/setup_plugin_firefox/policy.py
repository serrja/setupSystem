"""Publish a bounded merge into the system Firefox enterprise policy."""

import hashlib
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter

from setup_system.infrastructure.native_target import current_target
from setup_system.infrastructure.system_files import validate_system_path
from setup_system.infrastructure.system_paths import protected
from setup_system.infrastructure.system_publication import (
    ObservedFile,
    observe_file,
    publish_file,
    save_backup,
)
from setup_system.layout import require_installed_resource
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Resource

PATH = Path("/etc/firefox/policies/policies.json")
JSON = TypeAdapter(dict[str, JsonValue])
EXTENSION_ID = "uBlock0@raymondhill.net"


class Settings(PluginSettings):
    protection: Literal["strict", "standard"] = Field(default="strict", title="Tracking protection")
    https_only: bool = Field(default=True, title="HTTPS-only browsing")
    install_ublock: bool = Field(default=False, title="Install uBlock Origin from Mozilla Add-ons")


def table(record: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = record.setdefault(key, {})
    if not isinstance(value, dict):
        raise ValueError("Existing Firefox policy has a conflicting type")
    return value


def render(raw: bytes, settings: Settings) -> bytes:
    data = JSON.validate_json(raw) if raw else {}
    policies = table(data, "policies")
    tracking = table(policies, "EnableTrackingProtection")
    if tracking.get("Locked"):
        raise ValueError("An administrator already locked tracking protection")
    tracking.update({"Value": True, "Locked": False, "Cryptomining": True, "Fingerprinting": True})
    prefs = table(policies, "Preferences")
    for key, value in (
        ("dom.security.https_only_mode", settings.https_only),
        ("browser.contentblocking.category", settings.protection),
    ):
        setting = table(prefs, key)
        if setting.get("Status") == "locked":
            raise ValueError("An administrator already locked this preference")
        setting.update({"Value": value, "Status": "default"})
    if settings.install_ublock:
        extensions = table(policies, "ExtensionSettings")
        desired: dict[str, JsonValue] = {
            "installation_mode": "normal_installed",
            "install_url": f"https://addons.mozilla.org/firefox/downloads/latest/{EXTENSION_ID}/latest.xpi",
        }
        legacy: dict[str, JsonValue] = {
            **desired,
            "install_url": "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi",
        }
        if extensions.get(EXTENSION_ID, desired) not in (desired, legacy):
            raise ValueError("An existing extension policy requires review")
        extensions[EXTENSION_ID] = desired
    return JSON.dump_json(data, indent=2) + b"\n"


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


def private_directories(path: Path, raw: bytes | None) -> tuple[PrivateDirectory, ...]:
    private = []
    for directory, child in ((path.parent.parent, path.parent), (path.parent, path)):
        if directory.exists() and not directory.stat().st_mode & stat.S_IXOTH:
            info = directory.lstat()
            known = render(b"", Settings(install_ublock=True))
            if (
                raw is None
                or JSON.validate_json(
                    raw.replace(
                        b"/ublock-origin/latest.xpi", b"/uBlock0@raymondhill.net/latest.xpi"
                    )
                )
                != JSON.validate_json(known)
                or stat.S_IMODE(info.st_mode) != 0o700
                or set(directory.iterdir()) != {child}
            ):
                raise ValueError("Private Firefox policy directory has unknown contents")
            private.append(PrivateDirectory(directory, child, info.st_dev, info.st_ino))
    return tuple(private)


def inspect(path: Path, settings: Settings) -> FirefoxPolicy:
    before = observe_file(path)
    raw = before.data if before else None
    return FirefoxPolicy(path, before, render(raw or b"", settings), private_directories(path, raw))


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


def publish(
    plan: FirefoxPolicy,
    settings: Settings,
    *,
    backups: Path = Path("/var/lib/setup-system/settings-backups"),
) -> None:
    if inspect(plan.path, settings) != plan:
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


def snapshot(path: Path) -> tuple[bytes | None, int | None]:
    protected(path, directory=False)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, "rb") as stream:
        first = os.fstat(fd)
        if (
            first.st_uid != 0
            or not stat.S_ISREG(first.st_mode)
            or first.st_nlink != 1
            or first.st_mode & 0o022
        ):
            raise ValueError("Firefox policy path requires administrator inspection")
        data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or first.st_ctime_ns != os.fstat(fd).st_ctime_ns:
            raise ValueError("Firefox policy changed or exceeds the size limit")
        return data, stat.S_IMODE(first.st_mode)


def preview(settings: Settings) -> Plan:
    if not current_target()[1]:
        return Plan(
            outcome=Outcome.BLOCKED, summary="System Firefox policy requires supported Debian"
        )
    data, mode = snapshot(PATH)
    private = private_directories(PATH, data)
    digest = hashlib.sha256(render(data or b"", settings)).hexdigest()
    before = hashlib.sha256(data).hexdigest() if data is not None else None
    return Plan(
        outcome=Outcome.NO_CHANGES
        if before == digest and mode == 0o644 and not private
        else Outcome.READY,
        summary=f"Merge the reviewed Firefox policy into {PATH}",
        warnings=[f"Allow traversal to the known policy in {d.path}" for d in private],
        resources=[
            Resource(
                kind="file",
                identity=str(PATH),
                ownership="created" if data is None else "modified",
                before=before,
                after=digest,
                permissions=0o644,
                cleanup=data is None,
            )
        ],
    )


class Approval(Inputs):
    settings: Settings
    plan: Plan


class Authorization(Inputs):
    approval: Approval


def apply(approval: Approval) -> None:
    if os.geteuid() != 0 or not current_target()[1]:
        raise ValueError("System policy requires an authenticated Debian administrator")
    require_installed_resource(Path(__file__), "plugins/firefox/setup_plugin_firefox/policy.py")
    if preview(approval.settings) != approval.plan:
        raise ValueError("Firefox policy changed after confirmation")
    checked = inspect(PATH, approval.settings)
    row = approval.plan.resources[0]
    if (checked.before.digest if checked.before else None) != row.before or hashlib.sha256(
        checked.content
    ).hexdigest() != row.after:
        raise ValueError("Firefox policy changed during publication")
    publish(checked, approval.settings)


if __name__ == "__main__":
    raw = sys.stdin.buffer.read(262145)
    if len(raw) > 262144:
        sys.exit(2)
    apply(Authorization.model_validate_json(raw).approval)
