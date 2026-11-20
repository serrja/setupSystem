"""Boot preparation uses only private synthetic images, never the running host."""

import json
import os
from pathlib import Path

import pytest

from setup_system.infrastructure import graphical_unlock as unlock


@pytest.fixture
def boot_tree(tmp_path, monkeypatch):
    for name in ("BOOT", "ETC", "THEMES", "STATE"):
        destination = tmp_path / name.lower()
        destination.mkdir()
        monkeypatch.setattr(unlock, name, destination)
    for directory in ("grub",):
        (unlock.BOOT / directory).mkdir()
    for directory in ("plymouth", "default/grub.d", "initramfs-tools/conf.d"):
        (unlock.ETC / directory).mkdir(parents=True)
    (unlock.ETC / "crypttab").write_text("root UUID=test none luks\n")
    (unlock.BOOT / "initrd.img-6.1").write_bytes(b"previous image")
    (unlock.BOOT / "grub/grub.cfg").write_text("original grub.cfg")
    (unlock.ETC / "plymouth/plymouthd.conf").write_text("[Daemon]\nShowDelay=2\nTheme=spinner\n")
    monkeypatch.setattr(unlock, "prerequisites", lambda: ())
    monkeypatch.setattr(
        "setup_system.infrastructure.boot_recovery.protected", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(unlock, "_kernel", lambda: "6.1")
    monkeypatch.setattr(unlock, "_identity", lambda: "fixture")
    monkeypatch.setattr(unlock, "protected", lambda *args, **kwargs: None)
    real_euid = os.geteuid()
    monkeypatch.setattr(unlock.os, "geteuid", lambda: 0)
    # Keep the production atomic write implementation, with ownership checked against
    # the fixture account for all temporary test paths.
    monkeypatch.setattr(
        unlock.system,
        "_system_path",
        lambda path: (path.relative_to(tmp_path), path.parent.stat().st_uid == real_euid),
    )
    return tmp_path


def boot_commands(arguments):
    command = Path(arguments[0]).name
    if command == "lsinitramfs":
        return (
            "scripts/local-top/cryptroot\ncryptroot/crypttab\nusr/sbin/cryptsetup\n"
            "usr/share/plymouth/themes/setup-system-mocha/setup-system-mocha.plymouth\n"
            "usr/lib/plymouth/two-step.so\nusr/share/plymouth/themes/spinner/entry.png\n"
            "etc/console-setup/cached_UTF-8_del.kmap.gz\n"
        )
    if command == "mkinitramfs":
        Path(arguments[2]).write_bytes(b"rebuilt image")
    elif command == "grub-mkconfig":
        Path(arguments[2]).write_text(
            "set timeout_style=menu\nset timeout=5\n"
            "linux /vmlinuz-6.1 splash\nlinux /vmlinuz-6.1 single\n"
        )
    else:
        assert command == "grub-script-check"
    return ""


def test_rebuild_retains_backup_verifies_recovery_and_is_idempotent(boot_tree, monkeypatch):
    monkeypatch.setattr(unlock, "_command", boot_commands)
    unlock.apply_unlock("fixture")
    assert (unlock.BOOT / "initrd.img-6.1").read_bytes() == b"rebuilt image"
    assert "ShowDelay=2" in (unlock.ETC / "plymouth/plymouthd.conf").read_text()
    (record,) = unlock.STATE.glob("*/operation.json")
    receipt = json.loads(record.read_text())
    assert receipt["status"] == "needs_reboot"
    image = str(unlock.BOOT / "initrd.img-6.1")
    assert Path(receipt["files"][image]["backup"]).read_bytes() == b"previous image"
    unlock.apply_unlock("fixture")
    assert list(unlock.STATE.glob("*/operation.json")) == [record]


def test_failed_build_restores_theme_and_keeps_boot(boot_tree, monkeypatch):
    def fail_build(argv):
        if argv[0].endswith("grub-mkconfig"):
            raise RuntimeError("Synthetic GRUB build failure")
        return boot_commands(argv)

    monkeypatch.setattr(unlock, "_command", fail_build)
    with pytest.raises(RuntimeError, match="Synthetic"):
        unlock.apply_unlock("fixture")
    assert (unlock.BOOT / "initrd.img-6.1").read_bytes() == b"previous image"
    assert (unlock.BOOT / "grub/grub.cfg").read_text() == "original grub.cfg"
    assert "Theme=spinner" in (unlock.ETC / "plymouth/plymouthd.conf").read_text()
    assert not (unlock.ETC / "default/grub.d/98-setup-system-splash.cfg").exists()
    (record,) = unlock.STATE.glob("*/operation.json")
    assert json.loads(record.read_text())["status"] == "restored"


def test_noninteractive_crypttab_or_changed_target_stops_before_backup(boot_tree):
    (unlock.ETC / "crypttab").write_text("root UUID=test /secret/key luks\n")
    with pytest.raises(ValueError, match="interactive"):
        unlock.apply_unlock("fixture")
    with pytest.raises(ValueError, match="changed"):
        unlock.apply_unlock("different")
    assert not list(unlock.STATE.iterdir())


def test_grub_unlock_before_initramfs_is_identified(monkeypatch):
    monkeypatch.setattr(unlock, "_mount", lambda path: ("/dev/mapper/root", "ext4", "/"))
    monkeypatch.setattr(
        unlock, "_layers", lambda device: [["crypt", "ext4"], ["part", "crypto_LUKS"]]
    )
    assert "GRUB unlocks encrypted /boot" in unlock.inspect_unlock().blockers[0]


def test_prerequisite_install_starts_only_after_all_images_are_backed_up(boot_tree, monkeypatch):
    extra = unlock.BOOT / "initrd.img-5.9"
    extra.write_bytes(b"old kernel")
    needed = ["plymouth", "plymouth-themes"]
    monkeypatch.setattr(unlock, "prerequisites", lambda: tuple(needed))
    monkeypatch.setattr(unlock, "_command", boot_commands)

    def install(packages):
        (receipt,) = unlock.STATE.glob("*/operation.json")
        record = json.loads(receipt.read_text())
        assert record["status"] == "installing_prerequisites"
        assert Path(record["files"][str(extra)]["backup"]).read_bytes() == b"old kernel"
        assert packages == tuple(needed)
        extra.write_bytes(b"simulated Debian trigger")
        needed.clear()

    unlock.apply_unlock("fixture", packages=tuple(needed), install=install)
    (receipt,) = unlock.STATE.glob("*/operation.json")
    record = json.loads(receipt.read_text())
    assert record["status"] == "needs_reboot"
    assert record["files"][str(extra)]["verified"]


def test_recovery_preserves_external_admin_edit(boot_tree, monkeypatch):
    def external_edit(args):
        if Path(args[0]).name == "mkinitramfs":
            (unlock.ETC / "plymouth/plymouthd.conf").write_text("[Daemon]\nTheme=custom-admin\n")
            raise OSError("Synthetic rebuild interruption")
        return boot_commands(args)

    monkeypatch.setattr(unlock, "_command", external_edit)
    with pytest.raises(RuntimeError, match="preserved later edits"):
        unlock.apply_unlock("fixture")
    assert "custom-admin" in (unlock.ETC / "plymouth/plymouthd.conf").read_text()
    (receipt,) = unlock.STATE.glob("*/operation.json")
    assert json.loads(receipt.read_text())["status"] == "partial"
