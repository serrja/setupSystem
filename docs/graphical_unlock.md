# Graphical disk unlock

In **Base system**, select **Graphical unlock** to prepare Plymouth for an existing interactive LUKS root. The plan inspects the running kernel, root device stack, separate cleartext `/boot`, mounted EFI system partition, current Debian GRUB/shim boot entry and active KMS driver. It requires a working initramfs-tools/cryptsetup-initramfs boot, GRUB tools and efibootmgr on the target. If Plymouth, its spinner assets or console-setup are missing, the plan names the Debian packages it will acquire after backing up existing initramfs images. GRUB unlocking an encrypted `/boot` happens before the initramfs starts and remains outside this profile.

Apply checks the existing crypttab for interactive password entries and rejects keyfile and automatic-unlock configurations. It checks space for backups and a staged image. The selected Plymouth theme uses Debian's native two-step password dialog, the Mocha palette and the Debian spinner assets. The console keymap is included. Escape switches to the detailed native screen. Passwords stay in the boot prompt.

The executor stages a new image for the **running kernel**, lists its contents to verify cryptroot, the keymap and Plymouth, and checks a generated GRUB configuration for a recovery entry. It replaces the working image only after these checks. Debian package triggers may also rebuild other installed kernel images. Their original images are backed up before prerequisite installation. A successful operation reports **Needs reboot**. The user chooses when to reboot.

## Recovery

Root-owned directories under `/var/lib/setup-system/boot/unlock-*/` hold numbered backups and an `operation.json` with the original paths, modes and SHA-256 hashes. The executor compares current files with recorded after-hashes before restoring them following a build failure. If package installation started, Debian may retain package changes. Inspect the journal before retrying. For power loss, select an alternate kernel or a rescue environment, mount the target filesystems and compare the journal and backups to the current files before restoring. Keep the verified backup until both the normal and recovery entries have booted.

## Disposable target acceptance

1. Record the Debian release, architecture, kernel, EFI loader, LUKS root topology and separate boot mounts of the approved disposable VM. Use a synthetic disk password. Create an offline VM snapshot.
2. Boot the original kernel, verify normal text unlock and the recovery menu, and record the hashes of the running kernel's initramfs and GRUB configuration. Keep the evidence free of key material.
3. Open the exact released build as the normal user, inspect the plan, and approve the graphical unlock change. Compare the backup hashes with step 2, check the reported kernel version and list the generated initramfs with `lsinitramfs`.
4. Reboot manually. Verify a wrong password, a successful graphical unlock, the keymap and Escape fallback. Boot the recovery entry. Verify that the selected user's HOME, login and existing disk encryption still work.
5. Restore the snapshot for an insufficient-space or failed-build run. Verify the original boot hashes and boot into the original working system. Record target, artifact SHA, result and evidence for each architecture separately.

The isolated native gate exercises staging, readback, rollback and conflict handling on synthetic files. A Debian boot result requires the target exercise above. Debian documents the native [Plymouth controls](https://manpages.debian.org/trixie/plymouth/plymouth-set-default-theme.8.en.html) and [initramfs keymap option](https://manpages.debian.org/trixie/initramfs-tools-core/initramfs.conf.5.en.html).
