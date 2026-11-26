# 🧩 Plymouth

Plymouth graphical prompt with recovery.

## 🔐 Graphical disk password prompt

Plymouth supplies the initramfs prompt after GRUB loads the kernel. Install requires an existing LUKS root, a separate clear boot partition, Debian GRUB EFI, KMS and verified initramfs tooling. Preview lists files, package versions and recovery conditions. The privileged action backs up existing images before package triggers can run.

A completed file readback still needs a boot test. On an authorized disposable machine, confirm both graphical and text password entry and the GRUB recovery path. The extension never requests a disk password. Preserve the boot packages, backup and fallback during removal.
