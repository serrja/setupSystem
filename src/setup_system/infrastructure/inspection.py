"""Read-only hardware and boot observations; absence is not a successful GUI test."""

from pathlib import Path

from setup_system.domain.system_facts import EncryptionSnapshot, InspectionPaths, SystemSnapshot
from setup_system.infrastructure.clock import inspect_clock
from setup_system.infrastructure.encryption import (
    DirectoryFlags,
    filesystem_flags,
    home_encryption,
    root_encryption,
)
from setup_system.infrastructure.inspection_commands import (
    InspectionRunner,
    run_inspection,
    safe_absolute,
)


def inspect_system(
    home: Path,
    *,
    run: InspectionRunner = run_inspection,
    paths: InspectionPaths | None = None,
    home_flags: DirectoryFlags | None = None,
) -> SystemSnapshot:
    """Observe current protection/clock state; UNKNOWN never means unencrypted."""
    if not safe_absolute(str(home)):
        raise ValueError("System inspection requires an absolute HOME without traversal")
    paths = InspectionPaths() if paths is None else paths
    flags = (lambda path: filesystem_flags(path, run=run)) if home_flags is None else home_flags
    return SystemSnapshot(
        encryption=EncryptionSnapshot(
            root=root_encryption(Path("/"), run), home=home_encryption(home, run, flags)
        ),
        clock=inspect_clock(run, paths),
    )


def observations(
    home: Path, *, sys_root: Path = Path("/sys"), boot: Path = Path("/boot")
) -> tuple[str, ...]:
    facts = inspect_system(home)
    vendors = {"0x8086": "Intel", "0x1002": "AMD", "0x10de": "NVIDIA", "0x1af4": "Virtio"}
    gpu = set()
    for path in (sys_root / "class/drm").glob("card[0-9]*/device/vendor"):
        try:
            gpu.add(vendors.get(path.read_text().strip(), "unclassified GPU"))
        except OSError:
            gpu.add("unreadable GPU")
    connected = 0
    for path in (sys_root / "class/drm").glob("card*-*/status"):
        try:
            connected += path.read_text().strip() == "connected"
        except OSError:
            pass
    return (
        "GPU: " + (", ".join(sorted(gpu)) if gpu else "not observable; compatibility unverified"),
        f"Connected DRM outputs: {connected}; actual HiDPI/scale needs a graphical target.",
        f"Encryption: root={facts.encryption.root.state}, HOME={facts.encryption.home.state}; "
        "native unlock preserved.",
        f"Clock: timezone={facts.clock.timezone or 'unknown'}; NTP={facts.clock.ntp_enabled}.",
        "Boot: "
        + ("EFI observed; " if (sys_root / "firmware/efi").exists() else "EFI not observed; ")
        + ("GRUB observed" if (boot / "grub/grub.cfg").is_file() else "GRUB not observable")
        + "; unlock phase requires boot-specific validation.",
    )
