# Recovery

Cancel requests stop between safe stages. They do not kill dpkg or promise an APT rollback.
A failed operation reports completed stages and a nonzero exit. Fix the underlying
problem, inspect the receipts and retry the same selection.

## Receipts and backups

The privileged executor serializes system changes with a root-owned process lock.
Cleanup re-resolves APT, then uses dpkg remove on exactly the approved names; it cannot
expand to newly introduced dependencies. See `cleanup.json` for partial/failed attempts.

- `/var/lib/setup-system/install.json`: signed APT acquisition, closure and actual package stages.
- `/var/lib/setup-system/desktop.json` and `desktop-backups/`: managed login/boot evidence.
- `/var/lib/setup-system/settings-backups/` holds previous system intentions and managed Firefox policy bytes by content hash.
- `$XDG_STATE_HOME/setup-system/workstation.json` holds the user ownership index.
- `$XDG_STATE_HOME/setup-system/backups/` holds private file operation journals and original bytes.

When the system service replaces an existing system intentions file or managed Firefox
policy, it retains the original inode in a private sibling directory named
`.setup-system-<filename>-<suffix>/original`. An administrator who had the old
file open can still write to that inode. After a conflict or interruption, compare
the live file, `original` and the content-addressed backup before recovering the
intended bytes and permissions. A new file at the live path is preserved.

Open Workspace Tools, select Recovery and preview an operation. The preview lists
which files will be restored or removed. Apply checks the current hash, owner and
mode against the recorded state. A later edit requires review. A successful
restore removes empty directories created by that operation and leaves additional
files in place. You can repeat a completed restore.

The versioned journal records the expected changes before the user role runs.
The user role stages each replacement on the destination filesystem. For an existing
file it retains the original inode and checks its contents and ownership before
publishing. A newly arrived file keeps its place. Readback marks each verified
write. If a write was interrupted while the original was displaced, open Recovery
and preview that operation before retrying. Keep its private staging directory
until Restore finishes. A conflict can leave both the editor's version and the
displaced file available for inspection. Restore also retains the inode it removes. If an
editor later saves through a file descriptor it already had open, preview the same
operation to find those bytes. Legacy file journals are
converted on explicit recovery when their before/after evidence is sufficient.
Original journals are retained. Unknown schemas and ambiguous legacy ownership
require manual review. Private application databases use their application's
native recovery procedure.

System recovery depends on the stage that completed. Keep original release
artifacts. Use `dpkg --audit` to inspect an interrupted package operation and
review APT's proposed resolution before authorizing repair.

Before starting a new package transaction, the installer preserves a recognized schema 1
receipt at `/var/lib/setup-system/migration/install-v1-<sha256>.json`. Schema 2
records the current package stages. An unknown schema or a conflicting backup stops
the transaction so an administrator can inspect the original.

The graphical login plan records the current systemd default target, the readable
GRUB inputs and the intended login files before confirmation. Apply checks them
again using the verified privileged runtime. The plan names any inputs that only
root can read. A missing greetd unit is checked after its planned package installation.
Debian package triggers can regenerate the output menu from the same GRUB inputs.

For a failed graphical login, retain SSH or access through Ctrl+Alt+F3. Inspect
greetd's journal and `/var/lib/setup-system/desktop-backups/operation.json`. This
write-ahead journal identifies the previous boot target, whether greetd was enabled,
the hashes and modes before and after each file change, and the prepared inode.
Each entry names a private `.setup-system-login-*` directory on the same filesystem.
An interrupted change can leave `prepared`, `original` or `restored-after` there.
Retain them together with the recorded backups. Compare current ownership, mode and
hash against the journal before restoring a displaced file. A conflict can leave
both versions available for review. The managed destinations include the generated
GRUB menu and the management receipt itself.

For Plymouth, inspect `/var/lib/setup-system/boot/unlock-*/operation.json`.
New entries record the verified mode in `after_mode` alongside the after-hash.
The journal names private directories on each destination filesystem in `apply_area`
and `restore_area`. Apply verifies the displaced inode before publishing the
prepared file and leaves a competing new name in place. Restore checks the
published inode, contents and mode. Inspect the recorded areas for `prepared`
and `displaced` files after a conflict or interruption. Retain the original
initramfs and these directories until normal and recovery boots have succeeded.
Historical entries can omit these fields. Confirm their mode independently. Follow the [graphical unlock recovery procedure](graphical_unlock.md).
