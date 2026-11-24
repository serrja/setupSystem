"""Boot preparation uses only private synthetic images, never the running host."""

import json
import os
from pathlib import Path

import pytest

from setup_system.infrastructure import graphical_unlock as unlock
from setup_system.infrastructure import system_files


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
    monkeypatch.setattr(unlock, "_identity", lambda: "f" * 64)
    monkeypatch.setattr(unlock, "protected", lambda *args, **kwargs: None)
    real_euid = os.geteuid()
    monkeypatch.setattr(unlock.os, "geteuid", lambda: 0)
    # Keep the production atomic write implementation, with ownership checked against
    # the fixture account for all temporary test paths.
    monkeypatch.setattr(
        system_files,
        "validate_system_path",
        lambda path: (path.relative_to(tmp_path), path.parent.stat().st_uid == real_euid),
    )
    monkeypatch.setattr(unlock, "PACKAGE_ROOT", Path(__file__).resolve().parents[1])
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
    assert not unlock.is_current(None)
    assert unlock.apply_unlock("f" * 64)
    assert (unlock.BOOT / "initrd.img-6.1").read_bytes() == b"rebuilt image"
    assert "ShowDelay=2" in (unlock.ETC / "plymouth/plymouthd.conf").read_text()
    (record,) = unlock.STATE.glob("*/operation.json")
    theme = unlock.THEMES / unlock.THEME / (unlock.THEME + ".plymouth")
    config = unlock.configparser.ConfigParser()
    config.read(theme)
    colors = unlock.palette(unlock.PACKAGE_ROOT)
    assert config["Plymouth Theme"]["ModuleName"] == "two-step"
    assert config["two-step"]["ProgressBarForegroundColor"] == "0x" + colors["green"][1:]
    assert config["two-step"]["ImageDir"] == "/usr/share/plymouth/themes/spinner"
    receipt = json.loads(record.read_text())
    assert receipt["status"] == "needs_reboot"
    image = str(unlock.BOOT / "initrd.img-6.1")
    assert Path(receipt["files"][image]["backup"]).read_bytes() == b"previous image"
    assert unlock.is_current(None)
    assert not unlock.apply_unlock("f" * 64)
    assert list(unlock.STATE.glob("*/operation.json")) == [record]


def test_failed_build_restores_theme_and_keeps_boot(boot_tree, monkeypatch):
    def fail_build(argv):
        if argv[0].endswith("grub-mkconfig"):
            raise RuntimeError("Synthetic GRUB build failure")
        return boot_commands(argv)

    monkeypatch.setattr(unlock, "_command", fail_build)
    with pytest.raises(RuntimeError, match="Synthetic"):
        unlock.apply_unlock("f" * 64)
    assert (unlock.BOOT / "initrd.img-6.1").read_bytes() == b"previous image"
    assert (unlock.BOOT / "grub/grub.cfg").read_text() == "original grub.cfg"
    assert "Theme=spinner" in (unlock.ETC / "plymouth/plymouthd.conf").read_text()
    assert not (unlock.ETC / "default/grub.d/98-setup-system-splash.cfg").exists()
    (record,) = unlock.STATE.glob("*/operation.json")
    assert json.loads(record.read_text())["status"] == "restored"


def test_noninteractive_crypttab_or_changed_target_stops_before_backup(boot_tree):
    (unlock.ETC / "crypttab").write_text("root UUID=test /secret/key luks\n")
    with pytest.raises(ValueError, match="interactive"):
        unlock.apply_unlock("f" * 64)
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

    unlock.apply_unlock("f" * 64, packages=tuple(needed), install=install)
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
        unlock.apply_unlock("f" * 64)
    assert "custom-admin" in (unlock.ETC / "plymouth/plymouthd.conf").read_text()
    (receipt,) = unlock.STATE.glob("*/operation.json")
    assert json.loads(receipt.read_text())["status"] == "partial"


def test_rollback_preserves_a_later_admin_chmod(boot_tree, monkeypatch):
    path = unlock.ETC / "plymouth/plymouthd.conf"

    path.chmod(0o644)

    def run(args):
        if Path(args[0]).name == "mkinitramfs":
            path.chmod(0o600)
            raise OSError("injected failure after chmod")
        return boot_commands(args)

    monkeypatch.setattr(unlock, "_command", run)
    with pytest.raises(RuntimeError, match="preserved later edits"):
        unlock.apply_unlock("f" * 64)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("timing", ["observation", "claim"])
def test_boot_rollback_retains_same_content_replacement_during_claim(
    boot_tree, monkeypatch, timing
):
    from setup_system.infrastructure import boot_recovery

    path = unlock.ETC / "plymouth/plymouthd.conf"
    changed = []
    observe = boot_recovery.digest

    def replace_after_observation(source):
        result = observe(source)
        journals = list(unlock.STATE.glob("*/operation.json"))
        if (
            source == path
            and not changed
            and journals
            and json.loads(journals[0].read_bytes())["status"] == "restoring"
        ):
            new = path.with_suffix(".replacement")
            new.write_bytes(path.read_bytes())
            new.chmod(path.stat().st_mode & 0o777)
            new.replace(path)
            changed.append(path.stat().st_ino)
        return result

    def fail(args):
        if Path(args[0]).name == "mkinitramfs":
            raise OSError("injected rebuild failure")
        return boot_commands(args)

    if timing == "observation":
        monkeypatch.setattr(boot_recovery, "digest", replace_after_observation)
    else:
        move = boot_recovery.move_new

        def replace_during_claim(source, destination, **kwargs):
            if source == path:
                replace_after_observation(path)
            move(source, destination, **kwargs)

        monkeypatch.setattr(boot_recovery, "move_new", replace_during_claim)
    monkeypatch.setattr(unlock, "_command", fail)
    with pytest.raises(RuntimeError, match="preserved later edits"):
        unlock.apply_unlock("f" * 64)
    assert changed and path.stat().st_ino == changed[0]


@pytest.mark.parametrize("target", ["initrd", "new_config"])
def test_boot_apply_preserves_concurrent_file_during_publication(boot_tree, monkeypatch, target):
    from setup_system.infrastructure import boot_recovery

    image = unlock.BOOT / "initrd.img-6.1"
    new_config = unlock.ETC / "default/grub.d/98-setup-system-splash.cfg"
    selected = image if target == "initrd" else new_config
    replacement = b"later administrator contents\n"
    created = []

    def replace():
        file = selected.with_name("administrator-replacement")
        file.write_bytes(replacement)
        file.replace(selected)
        created.append(selected.stat().st_ino)

    checkpoint = boot_recovery.BootRecovery.checkpoint
    copy = boot_recovery.shutil.copyfile

    def after_checkpoint(operation, status):
        checkpoint(operation, status)
        if target == "new_config" and not created and new_config in operation.after:
            replace()

    def during_copy(source, destination):
        result = copy(source, destination)
        if target == "initrd" and Path(source).name == "initrd" and not created:
            replace()
        return result

    monkeypatch.setattr(boot_recovery.BootRecovery, "checkpoint", after_checkpoint)
    monkeypatch.setattr(boot_recovery.shutil, "copyfile", during_copy)
    monkeypatch.setattr(unlock, "_command", boot_commands)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        unlock.apply_unlock("f" * 64)
    assert created and selected.read_bytes() == replacement
    assert selected.stat().st_ino == created[0]
    assert (unlock.BOOT / "grub/grub.cfg").read_text() == "original grub.cfg"


def test_interrupted_boot_publication_returns_actual_displaced_inode(boot_tree, monkeypatch):
    from setup_system.infrastructure import boot_recovery

    path = unlock.BOOT / "initrd.img-6.1"
    original = path.stat()
    move = boot_recovery.move_new

    def fail_after_displacement(source, destination, **options):
        move(source, destination, **options)
        if source == path and destination.name == "displaced":
            raise OSError("injected interruption after displacement")

    monkeypatch.setattr(boot_recovery, "move_new", fail_after_displacement)
    monkeypatch.setattr(unlock, "_command", boot_commands)
    with pytest.raises(OSError, match="interruption"):
        unlock.apply_unlock("f" * 64)
    assert os.path.samestat(path.stat(), original)
    assert path.read_bytes() == b"previous image"
    (journal,) = unlock.STATE.glob("*/operation.json")
    assert json.loads(journal.read_bytes())["status"] == "restored"
