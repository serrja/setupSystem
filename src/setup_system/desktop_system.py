"""Planned graphical login, existing boot configuration and clock integration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Protocol

from setup_system.infrastructure.palette import palette
from setup_system.system_inspection import (
    EncryptionState,
    SystemSnapshot,
    inspect_system,
    validate_timezone,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]
XDG_KEYS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


class DesktopUser(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def uid(self) -> int: ...
    @property
    def gid(self) -> int: ...
    @property
    def home(self) -> Path: ...


@dataclass(frozen=True)
class DesktopPaths:
    boundary: Path = Path("/")
    config: Path = Path("/etc/greetd/setup-system.toml")
    unit_dropin: Path = Path("/etc/systemd/system/greetd.service.d/50-setup-system.conf")
    local_unit: Path = Path("/etc/systemd/system/greetd.service")
    vendor_unit: Path = Path("/usr/lib/systemd/system/greetd.service")
    display_manager: Path = Path("/etc/systemd/system/display-manager.service")
    default_target: Path = Path("/etc/systemd/system/default.target")
    registry: Path = Path("/etc/setup-system/login-users")
    bridge: Path = Path("/usr/local/libexec/setup-system/login-session")
    style: Path = Path("/usr/local/share/setup-system/login/greeter.css")
    receipt: Path = Path("/var/lib/setup-system/desktop.json")
    backups: Path = Path("/var/lib/setup-system/desktop-backups")
    grub_dropin: Path = Path("/etc/default/grub.d/99-setup-system.cfg")
    grub_defaults: Path = Path("/etc/default/grub.d")
    grub_default: Path = Path("/etc/default/grub")
    grub_scripts: Path = Path("/etc/grub.d")
    grub_config: Path = Path("/boot/grub/grub.cfg")
    update_grub: Path = Path("/usr/sbin/update-grub")
    zoneinfo: Path = Path("/usr/share/zoneinfo")


@dataclass(frozen=True)
class DesktopAccount:
    name: str
    uid: int
    gid: int
    home: Path


@dataclass(frozen=True)
class DesktopFile:
    path: Path
    content: bytes
    mode: int
    observed: str | None


@dataclass(frozen=True)
class DesktopPlan:
    target: DesktopAccount
    environment: Mapping[str, str]
    files: tuple[DesktopFile, ...]
    snapshot: SystemSnapshot
    timezone: str | None
    extra_packages: tuple[str, ...]
    enable_ntp: bool
    previous_target: str
    grub_observed: str
    receipt_observed: str | None
    deferred_checks: bool = False
    grub_managed: bool = False
    grub_inputs: str = ""
    manage_clock: bool = True

    def describe(self) -> tuple[str, ...]:
        encryption = self.snapshot.encryption
        ntp = (
            "enable existing/default provider"
            if self.enable_ntp
            else "preserve current configuration"
        )
        zone = self.timezone or self.snapshot.clock.timezone or "unknown"
        return (
            f"Existing encryption: root={encryption.root.state}; HOME={encryption.home.state}. "
            "No encryption changes.",
            f"Clock: timezone={zone}; NTP={ntp}. "
            "A clock adjustment cannot be transactionally undone.",
            "Graphical login: greetd + gtkgreet + Cage on VT7; "
            "authenticate normally, then start Hyprland as the selected user.",
            "Login starts at the next normal boot; no autologin, session switch or reboot.",
            "GRUB retained: visible menu, 5-second timeout and 10-second recovery timeout.",
            "Package installation can enable the vendor greetd alias before this final stage; "
            "service startup stays deferred.",
            *(f"Managed system file: {item.path}" for item in self.files),
            *(
                ("Protected desktop files need privileged revalidation before apply.",)
                if self.deferred_checks
                else ()
            ),
        )


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Graphical login and boot configuration require root")


class DesktopSystem:
    """Apply a checked finite system plan, with file recovery on command failure."""

    def __init__(
        self, root: Path, *, run: Runner, paths: DesktopPaths | None = None, owner_uid: int = 0
    ) -> None:
        self.root = root
        self.run = run
        self.paths = DesktopPaths() if paths is None else paths
        self.owner_uid = owner_uid

    def _parents(self, path: Path, *, leaf_link: bool = False) -> None:
        if not path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in str(path)):
            raise ValueError("Unsafe desktop system path")
        if not path.is_relative_to(self.paths.boundary):
            raise ValueError("Desktop path escapes its trusted boundary")
        for parent in (path, *path.parents):
            if not parent.is_relative_to(self.paths.boundary):
                break
            if not parent.exists() and not parent.is_symlink():
                continue
            details = parent.lstat()
            if stat.S_ISLNK(details.st_mode):
                if parent == path and leaf_link and details.st_uid == self.owner_uid:
                    continue
                raise ValueError("Desktop system path traverses a symlink")
            if details.st_uid != self.owner_uid or details.st_mode & 0o022:
                raise ValueError("Desktop system path is not administrator controlled")
            if parent != path and not stat.S_ISDIR(details.st_mode):
                raise ValueError("Desktop system parent is not a directory")

    def _read(self, path: Path, *, limit: int = 1024 * 1024) -> bytes | None:
        self._parents(path)
        try:
            details = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or details.st_size > limit:
            raise ValueError("Desktop system file has an unsafe type, link count or size")
        return path.read_bytes()

    def _fingerprint(self, path: Path, *, limit: int = 1024 * 1024) -> str | None:
        content = self._read(path, limit=limit)
        return None if content is None else hashlib.sha256(content).hexdigest()

    def _command(
        self, arguments: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = self.run(
            arguments,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
            text=True,
            capture_output=True,
            timeout=120,
            check=check,
        )
        if check and result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, arguments, result.stdout, result.stderr
            )
        return result

    def _manager(self) -> bool:
        paths = self.paths
        self._parents(paths.display_manager, leaf_link=True)
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

    def _grub(self) -> tuple[str, str]:
        paths = self.paths
        default = self._read(paths.grub_default)
        update = self._read(paths.update_grub)
        if default is None or update is None:
            raise ValueError("GRUB configuration and update-grub are required for recoverable boot")
        if not os.access(paths.update_grub, os.X_OK):
            raise ValueError("The installed update-grub is not executable")
        digest = self._fingerprint(paths.grub_config, limit=16 * 1024 * 1024)
        if digest is None:
            raise ValueError("No installed GRUB configuration; no bootloader will be installed")
        inputs: list[tuple[str, str, int]] = []

        def observe(path: Path, content: bytes) -> None:
            inputs.append(
                (str(path), hashlib.sha256(content).hexdigest(), stat.S_IMODE(path.stat().st_mode))
            )

        observe(paths.grub_default, default)
        observe(paths.update_grub, update)
        self._parents(paths.grub_scripts)
        for script in paths.grub_scripts.iterdir():
            if script.is_file() and os.access(script, os.X_OK):
                content = self._read(script)
                if content is not None:
                    observe(script, content)
        self._parents(paths.grub_defaults)
        for config in paths.grub_defaults.glob("*.cfg"):
            data = self._read(config)
            if data is not None:
                observe(config, data)
            if config.name > paths.grub_dropin.name and data and b"GRUB_" in data:
                raise ValueError("A later GRUB drop-in could override the requested boot policy")
        encoded = json.dumps(sorted(inputs), separators=(",", ":")).encode()
        return digest, hashlib.sha256(encoded).hexdigest()

    def _receipt(self) -> dict[str, dict[str, object]]:
        content = self._read(self.paths.receipt)
        if content is None:
            return {}
        document = json.loads(content)
        if (
            not isinstance(document, dict)
            or document.get("schema") != 1
            or document.get("owner") != "setupSystem"
        ):
            raise ValueError("Invalid desktop management receipt")
        files = document.get("files")
        if not isinstance(files, dict):
            raise ValueError("Invalid desktop management file receipt")
        result: dict[str, dict[str, object]] = {}
        for name, entry in files.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ValueError("Invalid desktop management file identity")
            digest, mode = entry.get("sha256"), entry.get("mode")
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or type(mode) is not int
            ):
                raise ValueError("Invalid desktop management file digest or mode")
            result[name] = {"sha256": digest, "mode": mode}
        return result

    def _greeter_style(self) -> bytes:
        content = (self.root / "assets/login/greeter.css").read_text()
        if "$" in content:
            try:
                content = Template(content).substitute(
                    palette(self.root / "src/setup_system/resources")
                )
            except KeyError as exc:
                raise ValueError("Unknown greeter palette field") from exc
        return content.encode()

    def plan(
        self,
        target: DesktopUser,
        environment: Mapping[str, str],
        *,
        timezone: str | None = None,
        snapshot: SystemSnapshot | None = None,
        manage_clock: bool = True,
    ) -> DesktopPlan:
        if (
            target.uid <= 0
            or target.gid < 0
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", target.name)
        ):
            raise ValueError("Graphical sessions require a verified ordinary account")
        home = target.home
        selected: dict[str, str] = {}
        for key in XDG_KEYS:
            value = Path(environment[key])
            if (
                not value.is_absolute()
                or ".." in value.parts
                or value == home
                or not value.is_relative_to(home)
            ):
                raise ValueError("Login XDG roots must stay within the verified HOME")
            selected[key] = str(value)
        account = DesktopAccount(target.name, target.uid, target.gid, home)
        snapshot = inspect_system(home) if snapshot is None else snapshot
        home_encryption = snapshot.encryption.home
        if home_encryption.state == EncryptionState.LOCKED or (
            home_encryption.state == EncryptionState.UNKNOWN
            and any(layer.casefold() in {"fscrypt", "ecryptfs"} for layer in home_encryption.layers)
        ):
            raise ValueError(
                "The encrypted HOME is locked or its unlock status is unknown; "
                "unlock and verify it before user runtime setup"
            )
        timezone = validate_timezone(timezone, zoneinfo_root=self.paths.zoneinfo)
        clock = snapshot.clock
        extra = (
            ("systemd-timesyncd",)
            if manage_clock
            and clock.providers_known
            and not clock.providers
            and clock.can_ntp is False
            and clock.ntp_enabled is False
            else ()
        )
        enable_ntp = (
            manage_clock and clock.ntp_enabled is False and (bool(extra) or clock.can_ntp is True)
        )
        paths = self.paths
        deferred = False
        try:
            self._manager()
            grub, grub_inputs = self._grub()
        except PermissionError:
            if os.geteuid() == 0:
                raise
            deferred = True
            grub, grub_inputs = "unreadable", "unreadable"
        previous = self._command(
            ["/usr/bin/systemctl", "--system", "--no-pager", "--no-ask-password", "get-default"]
        ).stdout.strip()
        if re.fullmatch(r"[A-Za-z0-9_.@:-]+\.target", previous) is None:
            raise ValueError("Cannot identify the current system boot target")
        registry = (
            json.dumps(
                {
                    "schema": 1,
                    "name": target.name,
                    "uid": target.uid,
                    "gid": target.gid,
                    "home": str(home),
                    "environment": selected,
                },
                sort_keys=True,
                indent=2,
            ).encode()
            + b"\n"
        )
        bridge_source = (
            (self.root / "assets/login/login_session.py")
            .read_text()
            .replace('"@LOGIN_REGISTRY@"', repr(str(paths.registry)))
            .encode()
        )
        command = (
            "/usr/bin/cage -s -- /usr/bin/gtkgreet -s "
            + shlex.quote(str(paths.style))
            + " -c "
            + shlex.quote(str(paths.bridge))
        )
        config = (
            "[general]\nsource_profile = false\n\n[terminal]\nvt = 7\nswitch = true\n\n"
            f'[default_session]\ncommand = {json.dumps(command)}\nuser = "_greetd"\n'
        ).encode()
        systemd_path = json.dumps(str(paths.config).replace("%", "%%"))
        dropin = (
            f"[Service]\nExecStart=\nExecStart=/usr/sbin/greetd --config {systemd_path}\n".encode()
        )
        desired = (
            (paths.config, config, 0o644),
            (paths.unit_dropin, dropin, 0o644),
            (paths.registry / f"{target.uid}.json", registry, 0o644),
            (paths.bridge, bridge_source, 0o755),
            (paths.style, self._greeter_style(), 0o644),
            (
                paths.grub_dropin,
                b"# Managed by setupSystem. Keep existing boot entries and crypto settings.\n"
                b"GRUB_TIMEOUT_STYLE=menu\nGRUB_TIMEOUT=5\nGRUB_RECORDFAIL_TIMEOUT=10\n",
                0o644,
            ),
        )
        grub_managed = False
        try:
            receipt = self._receipt()
            receipt_observed = self._fingerprint(paths.receipt)
            previous_receipt = self._read(paths.receipt)
            grub_managed = previous_receipt is not None and all(
                json.loads(previous_receipt).get(key) == observed
                for key, observed in (
                    ("grub_sha256", grub),
                    ("grub_inputs_sha256", grub_inputs),
                )
            )
        except PermissionError:
            if os.geteuid() == 0:
                raise
            receipt, receipt_observed, deferred = {}, None, True
        files: list[DesktopFile] = []
        for path, content, mode in desired:
            try:
                observed = self._fingerprint(path)
            except PermissionError:
                if os.geteuid() == 0:
                    raise
                observed, deferred = None, True
            if observed is not None and not deferred:
                recorded = receipt.get(str(path))
                desired_hash = hashlib.sha256(content).hexdigest()
                if observed != desired_hash and (
                    recorded is None or recorded["sha256"] != observed
                ):
                    raise ValueError(f"Unknown or edited desktop file preserved for review: {path}")
            files.append(DesktopFile(path, content, mode, observed))
        return DesktopPlan(
            account,
            selected,
            tuple(files),
            snapshot,
            timezone,
            extra,
            enable_ntp,
            previous,
            grub,
            receipt_observed,
            deferred,
            grub_managed,
            grub_inputs,
            manage_clock,
        )

    def revalidate(self, plan: DesktopPlan) -> DesktopPlan:
        """Accept regenerated GRUB output after packages, preserving consented inputs."""
        _require_root()
        fresh = self.plan(
            plan.target,
            plan.environment,
            timezone=plan.timezone,
            snapshot=plan.snapshot,
            manage_clock=plan.manage_clock,
        )

        def intended(value: DesktopPlan) -> tuple[tuple[Path, bytes, int], ...]:
            return tuple((item.path, item.content, item.mode) for item in value.files)

        if (
            intended(fresh) != intended(plan)
            or fresh.previous_target != plan.previous_target
            or fresh.extra_packages != plan.extra_packages
            or (
                not plan.deferred_checks
                and (
                    fresh.grub_inputs != plan.grub_inputs
                    or fresh.receipt_observed != plan.receipt_observed
                    or tuple(item.observed for item in fresh.files)
                    != tuple(item.observed for item in plan.files)
                )
            )
        ):
            raise ValueError("Desktop or GRUB inputs changed after consent; review a fresh plan")
        return fresh

    def _write(
        self,
        path: Path,
        content: bytes,
        mode: int,
        *,
        private: bool = False,
        limit: int = 1024 * 1024,
    ) -> None:
        self._parents(path)
        if len(content) > limit:
            raise ValueError("Managed desktop content exceeds its size limit")
        current = self._read(path, limit=limit)
        if current == content and stat.S_IMODE(path.stat().st_mode) == mode:
            return
        missing: list[Path] = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700 if private else 0o755)
        self._parents(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".setup-desktop-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _backup(self, path: Path, content: bytes, mode: int) -> None:
        identity = hashlib.sha256(str(path).encode()).hexdigest()
        digest = hashlib.sha256(content).hexdigest()
        saved = self.paths.backups / identity / digest
        existing = self._read(saved, limit=16 * 1024 * 1024)
        if existing is None:
            self._write(saved, content, 0o600, private=True, limit=16 * 1024 * 1024)
            self._write(
                saved.with_suffix(".json"),
                json.dumps({"path": str(path), "mode": mode, "sha256": digest}).encode() + b"\n",
                0o600,
                private=True,
            )
        elif existing != content:
            raise ValueError("Desktop backup conflicts with its recorded digest")

    def apply(self, plan: DesktopPlan) -> None:
        _require_root()
        if plan.deferred_checks:
            raise ValueError("Desktop plan needs a privileged revalidation before apply")
        self._manager()
        if (
            self._grub() != (plan.grub_observed, plan.grub_inputs)
            or self._fingerprint(self.paths.receipt) != plan.receipt_observed
        ):
            raise ValueError("Desktop or GRUB state changed after planning")
        for item in plan.files:
            if self._fingerprint(item.path) != item.observed:
                raise ValueError("Managed desktop destination changed after planning")
        launcher = Path(plan.environment["XDG_DATA_HOME"]) / "setup-system/bin/start-hyprland"
        if (
            launcher.is_symlink()
            or not launcher.is_file()
            or launcher.stat().st_uid != plan.target.uid
        ):
            raise ValueError("The selected user session launcher is missing or unsafe")
        originals: list[tuple[Path, bytes | None, int]] = []
        grub_before = self._read(self.paths.grub_config, limit=16 * 1024 * 1024)
        grub_mode = stat.S_IMODE(self.paths.grub_config.stat().st_mode)
        assert grub_before is not None
        enabled = False
        default_changed = False
        clock_changed = False
        ntp_changed = False
        try:
            for item in plan.files:
                old = self._read(item.path)
                old_mode = stat.S_IMODE(item.path.stat().st_mode) if old is not None else item.mode
                if old == item.content and old_mode == item.mode:
                    continue
                if old is not None:
                    self._backup(item.path, old, old_mode)
                originals.append((item.path, old, old_mode))
                self._write(item.path, item.content, item.mode)
            if not plan.grub_managed:
                self._backup(self.paths.grub_config, grub_before, grub_mode)
                self._command([str(self.paths.update_grub)])
            if originals:
                self._command(
                    [
                        "/usr/bin/systemctl",
                        "--system",
                        "--no-pager",
                        "--no-ask-password",
                        "daemon-reload",
                    ]
                )
            if not self._manager():
                enabled = True
                self._command(
                    [
                        "/usr/bin/systemctl",
                        "--system",
                        "--no-pager",
                        "--no-ask-password",
                        "enable",
                        "greetd.service",
                    ]
                )
            if plan.previous_target != "graphical.target":
                default_changed = True
                self._command(
                    [
                        "/usr/bin/systemctl",
                        "--system",
                        "--no-pager",
                        "--no-ask-password",
                        "set-default",
                        "graphical.target",
                    ]
                )
            if plan.timezone is not None and plan.timezone != plan.snapshot.clock.timezone:
                clock_changed = True
                self._command(["/usr/bin/timedatectl", "set-timezone", plan.timezone])
            if plan.enable_ntp:
                ntp_changed = True
                self._command(["/usr/bin/timedatectl", "set-ntp", "true"])
            receipt = self._receipt()
            receipt.update(
                {
                    str(item.path): {
                        "sha256": hashlib.sha256(item.content).hexdigest(),
                        "mode": item.mode,
                    }
                    for item in plan.files
                }
            )
            self._write(
                self.paths.receipt,
                json.dumps(
                    {
                        "schema": 1,
                        "owner": "setupSystem",
                        "files": receipt,
                        "grub_sha256": self._fingerprint(
                            self.paths.grub_config, limit=16 * 1024 * 1024
                        ),
                        "grub_inputs_sha256": self._grub()[1],
                    },
                    sort_keys=True,
                    indent=2,
                ).encode()
                + b"\n",
                0o600,
                private=True,
            )
        except BaseException as error:
            recovery: list[str] = []
            for path, old, mode in reversed(originals):
                try:
                    if old is None:
                        self._parents(path)
                        path.unlink(missing_ok=True)
                    else:
                        self._write(path, old, mode)
                except (OSError, ValueError) as exc:
                    recovery.append(type(exc).__name__)
            try:
                self._write(self.paths.grub_config, grub_before, grub_mode, limit=16 * 1024 * 1024)
            except (OSError, ValueError) as exc:
                recovery.append(type(exc).__name__)
            commands: list[list[str]] = []
            if default_changed:
                commands.append(
                    ["/usr/bin/systemctl", "--system", "set-default", plan.previous_target]
                )
            if enabled:
                commands.append(["/usr/bin/systemctl", "--system", "disable", "greetd.service"])
            if ntp_changed and plan.snapshot.clock.ntp_enabled is False:
                commands.append(["/usr/bin/timedatectl", "set-ntp", "false"])
            if clock_changed and plan.snapshot.clock.timezone:
                commands.append(
                    ["/usr/bin/timedatectl", "set-timezone", plan.snapshot.clock.timezone]
                )
            commands.append(["/usr/bin/systemctl", "--system", "daemon-reload"])
            for arguments in commands:
                try:
                    self._command(arguments)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    recovery.append(type(exc).__name__)
            if recovery:
                raise RuntimeError(
                    "Desktop stage failed; file recovery incomplete: " + ", ".join(recovery)
                ) from error
            raise
