"""Stage a Plymouth image for an existing Debian GRUB/initramfs-tools password unlock."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from setup_system.infrastructure.native_templates import render_asset
from setup_system.infrastructure.palette import palette
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.system_paths import protected
from setup_system.layout import resource_root

from .models import UnlockPlan
from .recovery import BootRecovery
from .recovery import digest as digest

BOOT = Path("/boot")
SYS = Path("/sys")
ETC = Path("/etc")
STATE = Path("/var/lib/setup-system/boot")
THEMES = Path("/usr/share/plymouth/themes")
THEME = "setup-system-mocha"
MIN_SPACE = 64 * 1024 * 1024
CMDLINE = Path("/proc/cmdline")
BOOT_TOOLS = (
    Path("/usr/sbin/mkinitramfs"),
    Path("/usr/bin/lsinitramfs"),
    Path("/usr/sbin/grub-mkconfig"),
    Path("/usr/bin/grub-script-check"),
    Path("/usr/sbin/cryptsetup"),
    Path("/usr/share/initramfs-tools/hooks/cryptroot"),
)


def _command(args: list[str]) -> str:
    return run_command(
        args,
        check=True,
        discard_output=args[0]
        in {"/usr/sbin/mkinitramfs", "/usr/sbin/grub-mkconfig", "/usr/bin/grub-script-check"},
        discard_stderr=True,
        max_output_bytes=16 * 1024 * 1024 if args[0] == "/usr/bin/lsinitramfs" else None,
        timeout=15
        if args[0] in {"/usr/bin/findmnt", "/usr/bin/lsblk", "/usr/bin/efibootmgr"}
        else 600,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C"},
    ).stdout


def _mount(path: str) -> tuple[str, str, str]:
    info = _command(
        [
            "/usr/bin/findmnt",
            "--noheadings",
            "--raw",
            "--output",
            "SOURCE,FSTYPE,TARGET",
            "--target",
            path,
        ]
    ).split()
    if len(info) != 3 or not re.fullmatch(r"/dev/[a-zA-Z0-9_./+-]+", info[0]):
        raise ValueError(f"Cannot identify a block device for {path}")
    return info[0], info[1], info[2]


def _layers(source: str) -> list[list[str]]:
    return [
        line.split()
        for line in _command(
            [
                "/usr/bin/lsblk",
                "--inverse",
                "--noheadings",
                "--raw",
                "--output",
                "TYPE,FSTYPE",
                source,
            ]
        ).splitlines()
    ]


def _efi_loader() -> str:
    lines = _command(["/usr/bin/efibootmgr", "-v"]).splitlines()
    current = next(
        (
            line.removeprefix("BootCurrent: ").strip()
            for line in lines
            if line.startswith("BootCurrent: ")
        ),
        "",
    )
    if re.fullmatch(r"[a-fA-F0-9]{4}", current):
        for line in lines:
            if line.lower().startswith("boot" + current.lower() + "*") and re.search(
                r"\\efi\\debian\\(?:shim|grub)(?:x64|aa64)\.efi\b", line, re.IGNORECASE
            ):
                return current
    raise ValueError("The current EFI boot entry must identify the Debian GRUB loader")


def _kernel() -> str:
    kernel = platform.release()
    if not re.fullmatch(r"[0-9][a-zA-Z0-9.+_-]{0,100}", kernel):
        raise ValueError("Unsupported kernel release")
    return kernel


def _identity() -> str:
    kernel = _kernel()
    root, boot, esp = _mount("/"), _mount("/boot"), _mount("/boot/efi")
    root_layers = _layers(root[0])
    if not any("crypt" in row for row in root_layers) or not any(
        "crypto_LUKS" in row for row in root_layers
    ):
        raise ValueError("Root must already use LUKS with initramfs password unlock")
    boot_layers = _layers(boot[0])
    if (
        boot[2] != "/boot"
        or not boot_layers
        or any(not row or row[0] not in {"part", "disk"} for row in boot_layers)
    ):
        raise ValueError(
            "GRUB unlocks encrypted /boot before Plymouth; a separate clear /boot is required"
        )
    if esp[2] != "/boot/efi" or esp[1] != "vfat" or not (SYS / "firmware/efi").is_dir():
        raise ValueError("A mounted EFI system partition and active Debian GRUB EFI are required")
    loader = _efi_loader()
    if "nomodeset" in CMDLINE.read_text().split() or not any(
        (SYS / "class/drm").glob("card[0-9]*/device/driver")
    ):
        raise ValueError("A kernel modesetting graphics driver is required")
    for tool in BOOT_TOOLS:
        if not tool.is_file():
            raise ValueError(f"Missing Debian boot prerequisite: {tool}")
    files = (
        BOOT / ("initrd.img-" + kernel),
        BOOT / ("vmlinuz-" + kernel),
        BOOT / "grub/grub.cfg",
        ETC / "crypttab",
    )
    stamps = []
    for path in files:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise ValueError(f"Expected an installed boot file at {path}")
        stamps.append(
            (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
        )
    identity = json.dumps([root, boot, esp, loader, kernel, stamps], sort_keys=True)
    return hashlib.sha256(identity.encode()).hexdigest()


def prerequisites() -> tuple[str, ...]:
    probes = TypeAdapter(dict[str, str]).validate_json(
        Path(__file__).with_name("probes.json").read_bytes()
    )
    return tuple(name for name, path in probes.items() if not Path(path).is_file())


def inspect_unlock() -> UnlockPlan:
    try:
        identity = _identity()
        try:
            _check_password_unlock()
        except PermissionError:
            # Debian may restrict crypttab to root. The privileged action reads it again.
            if os.geteuid() == 0:
                raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Native boot metadata could not be inspected"
        )
        return UnlockPlan(
            description="Graphical initramfs password prompt", identity=None, blockers=(reason,)
        )
    needed = prerequisites()
    dependency = "; install " + ", ".join(needed) + " after backup" if needed else ""
    return UnlockPlan(
        description=f"Rebuild {_kernel()} initramfs with a backup and verify GRUB recovery"
        + dependency,
        identity=identity,
        packages=needed,
    )


def _theme(root: Path | None = None) -> bytes:
    return render_asset(
        Path(__file__).with_name("mocha.plymouth"), palette(root or resource_root())
    )


def _theme_config(raw: bytes) -> bytes:
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(raw.decode())
    if not config.has_section("Daemon"):
        return raw.rstrip(b"\n") + f"\n[Daemon]\nTheme={THEME}\n".encode()
    lines = raw.decode().splitlines(keepends=True)
    active = False
    for index, line in enumerate(lines):
        if line.strip().startswith("["):
            if active:
                lines.insert(index, "Theme=" + THEME + "\n")
                break
            active = line.strip() == "[Daemon]"
        elif active and re.match(r"^\s*Theme\s*=", line):
            lines[index] = "Theme=" + THEME + "\n"
            break
    else:
        lines.append("\nTheme=" + THEME + "\n")
    return "".join(lines).encode()


def _verify_listing(image: Path, *, themed: bool) -> None:
    names = {
        item.removeprefix("./")
        for item in _command(["/usr/bin/lsinitramfs", str(image)]).splitlines()
    }
    if not (
        {"cryptroot/crypttab", "scripts/local-top/cryptroot"} <= names
        and any(name.endswith("/cryptsetup") for name in names)
    ):
        raise ValueError("Image lacks the native cryptroot password hooks")
    if themed and not (
        f"usr/share/plymouth/themes/{THEME}/{THEME}.plymouth" in names
        and any(x.endswith("/two-step.so") for x in names)
        and "usr/share/plymouth/themes/spinner/entry.png" in names
        and any(
            x.endswith("/keymap") or x.endswith("/cached.kmap.gz") or "cached_UTF-8" in x
            for x in names
        )
    ):
        raise ValueError("New initramfs lacks theme, native dialog assets or console keymap")


def _check_password_unlock() -> None:
    rows = [
        line.split()
        for line in (ETC / "crypttab").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not rows or any(
        len(row) not in {3, 4}
        or row[2] not in {"none", "-"}
        or (
            len(row) == 4
            and any(
                flag.partition("=")[0]
                not in {"luks", "discard", "initramfs", "nofail", "tries", "timeout"}
                for flag in row[3].split(",")
            )
        )
        for row in rows
    ):
        raise ValueError("Only existing interactive crypttab password entries are supported")


def _desired(root: Path | None) -> dict[Path, bytes]:
    config = ETC / "plymouth/plymouthd.conf"
    theme = THEMES / THEME / (THEME + ".plymouth")
    splash = ETC / "default/grub.d/98-setup-system-splash.cfg"
    keymap = ETC / "initramfs-tools/conf.d/setup-system-keymap"
    return {
        theme: _theme(root),
        splash: (
            b'case " $GRUB_CMDLINE_LINUX_DEFAULT " in *" splash "*) ;;\n'
            b'*) GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} splash" ;; esac\n'
            b'GRUB_TIMEOUT_STYLE=menu\ncase "$GRUB_TIMEOUT" in ""|0) GRUB_TIMEOUT=5 ;; esac\n'
        ),
        keymap: b"KEYMAP=y\n",
        config: _theme_config(config.read_bytes() if config.exists() else b""),
    }


def _verification_path() -> Path:
    return ETC / "plymouth/setup-system-unlock.verified"


def _verification_stamp() -> bytes:
    return ("setup-system-unlock-v1 " + _identity() + "\n").encode()


def is_current(root: Path | None) -> bool:
    if prerequisites():
        return False
    kernel = _kernel()
    try:
        if any(
            not path.is_file() or path.read_bytes() != data for path, data in _desired(root).items()
        ):
            return False
        if os.geteuid() != 0 and not all(
            os.access(p, os.R_OK) for p in (BOOT / ("initrd.img-" + kernel), BOOT / "grub/grub.cfg")
        ):
            stamp = _verification_path()
            protected(stamp, directory=False)
            return stamp.read_bytes() == _verification_stamp()
        _verify_listing(BOOT / ("initrd.img-" + kernel), themed=True)
        _verify_grub(BOOT / "grub/grub.cfg", kernel)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return True


def apply_unlock(
    expected: str | None,
    *,
    root: Path | None = None,
    packages: tuple[str, ...] | list[str] = (),
    install: Callable[[tuple[str, ...]], None] | None = None,
) -> bool:
    if os.geteuid() != 0 or _identity() != expected:
        raise ValueError("Boot state changed or privileged revalidation failed")
    needed = prerequisites()
    if (
        not isinstance(packages, (list, tuple))
        or tuple(packages) != needed
        or (needed and install is None)
    ):
        raise ValueError("Boot package prerequisites changed; inspect a new plan")
    _check_password_unlock()
    kernel = _kernel()
    image = BOOT / ("initrd.img-" + kernel)
    grub = BOOT / "grub/grub.cfg"
    config = ETC / "plymouth/plymouthd.conf"
    desired = _desired(root)
    images = tuple(sorted(BOOT.glob("initrd.img-*"))) if install else (image,)
    stamp = _verification_path()
    if stamp.exists() and not re.fullmatch(
        rb"setup-system-unlock-v1 [a-f0-9]{64}\n", stamp.read_bytes()
    ):
        raise ValueError("Unknown boot verification record")
    destinations = (*images, grub, *desired, stamp)
    for path in destinations:
        protected(path, directory=False)
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError("Unsupported boot file type or link count")
    for path, data in desired.items():
        if path != config and path.exists() and path.read_bytes() != data:
            raise ValueError(f"Managed boot destination conflicts with existing {path}")
    _verify_listing(image, themed=False)
    protected(STATE)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_bytes = sum(path.stat().st_size for path in destinations if path.exists())
    rebuild_bytes = 3 * max(path.stat().st_size for path in images) + MIN_SPACE
    same_device = BOOT.stat().st_dev == STATE.stat().st_dev
    if shutil.disk_usage(STATE).free < backup_bytes + MIN_SPACE or shutil.disk_usage(
        BOOT
    ).free < rebuild_bytes + (backup_bytes if same_device else 0):
        raise ValueError("Insufficient space for both verified backups and rebuilt boot images")
    if install is None and is_current(root):
        return False
    backup_dir = Path(tempfile.mkdtemp(prefix="unlock-", dir=STATE))
    recovery = BootRecovery(backup_dir, kernel, destinations)
    try:
        if _identity() != expected:
            raise ValueError("Boot state changed while backing up the reviewed configuration")
        config_backup = recovery.originals[config]
        desired[config] = _theme_config(config_backup[0].read_bytes() if config_backup else b"")
        for path, content in desired.items():
            if path != config and recovery.before[path] not in {
                None,
                hashlib.sha256(content).hexdigest(),
            }:
                raise ValueError(f"Managed boot destination changed during preparation: {path}")
        if install is not None:
            recovery.checkpoint("installing_prerequisites")
            install(needed)
            if prerequisites():
                raise RuntimeError("Debian boot package installation did not provide its hooks")
            # Debian package triggers may rebuild existing images and GRUB. Keep their
            # pre-install backups, and explicitly track each observed post-install result.
            for path in (*images, grub, config):
                observed = digest(path)
                if observed != recovery.before[path]:
                    if observed is None:
                        raise RuntimeError("A boot package removed a previously installed file")
                    recovery.after[path] = observed
                    recovery.after_modes[path] = stat.S_IMODE(path.lstat().st_mode)
                    recovery.verify(path)
            desired[config] = _theme_config(config.read_bytes() if config.exists() else b"")
        for path, data in desired.items():
            if path.exists() and path.read_bytes() == data:
                continue
            if not path.parent.exists():
                path.parent.mkdir(mode=0o755)
            prior = recovery.originals[path]
            recovery.publish(path, data, prior[1] if prior else 0o644)
        with tempfile.TemporaryDirectory(prefix=".setup-system-", dir=BOOT) as directory:
            candidate = Path(directory) / "initrd"
            fresh_grub = Path(directory) / "grub.cfg"
            _command(["/usr/sbin/mkinitramfs", "-o", str(candidate), kernel])
            _verify_listing(candidate, themed=True)
            _command(["/usr/sbin/grub-mkconfig", "-o", str(fresh_grub)])
            _verify_grub(fresh_grub, kernel)
            for path, prepared in ((image, candidate), (grub, fresh_grub)):
                prior = recovery.originals[path]
                if prior is None:
                    raise ValueError("Required boot file disappeared during rebuild")
                recovery.publish(path, prepared, prior[1])
        if not is_current(root):
            raise ValueError("Installed boot configuration did not pass readback")
        recovery.publish(stamp, _verification_stamp(), 0o644)
        recovery.checkpoint("needs_reboot")
        return True
    except BaseException:
        recovery.restore(packages_attempted=install is not None)
        raise


def _verify_grub(path: Path, kernel: str) -> None:
    _command(["/usr/bin/grub-script-check", str(path)])
    grub = path.read_text()
    commands = [
        line.split()
        for line in grub.splitlines()
        if line.lstrip().startswith(("linux ", "linux\t"))
    ]
    current = [line for line in commands if Path(line[1]).name == "vmlinuz-" + kernel]
    if (
        not any("splash" in line for line in current)
        or not any("single" in line or "recovery" in line for line in current)
        or not re.search(r"(?m)^\s*set timeout_style=menu\s*$", grub)
        or not re.search(r"(?m)^\s*set timeout=(?:[1-9][0-9]*|-1)\s*$", grub)
    ):
        raise ValueError(
            "GRUB must retain a reachable recovery entry and enable splash for the rebuilt kernel"
        )
