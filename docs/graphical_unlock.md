# Graphical unlock

Existing dm-crypt/LUKS and HOME encryption remain intact. Textual never requests, reads,
logs or stores a disk password. It does not format disks, modify LUKS slots, enable
autounlock or place a key on unencrypted storage.

A Plymouth password dialog can replace an initramfs prompt only when the existing
cryptsetup/initramfs integration actually owns the unlock. It cannot replace a GRUB
password prompt that occurs before initramfs. Preserve the native text fallback and
an accessible GRUB recovery menu.

The Plymouth selection currently blocks Apply until bootloader/initramfs recovery has
been validated on an explicitly selected disposable target. Root and HOME encryption
inspection exists, but it is not sufficient evidence for safely rebuilding an arbitrary
machine's initramfs. No boot change is performed on the development host.
