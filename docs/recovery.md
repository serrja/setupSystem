# 🧹 Recovery and ownership

Open **Managed resources** on the plugin page to inspect verified resources and unresolved operations. After an interruption, choose **Check interrupted operations**. The manager inspects the approved release and current facts before recording a verified result. A lost connection during a mutation leaves an unknown outcome. Keep the worker and its environment until inspection completes.

**Uninstall application** uses the package's native removal action. **Remove extension** retires sealed code after checking for local changes. **Full cleanup** lists remote, local and program actions, exact settings and keyring references, and code generations. Review the list of preserved resources. Cleanup can stop at a conflict or failed readback. Keep the evidence and use a fresh preview to continue.

The resource ledger is `$XDG_STATE_HOME/setup-system/resources.sqlite3`. It records prepared changes, verified fingerprints, consumers and unresolved reservations. The bounded JSONL journal in `$XDG_STATE_HOME/setup-system/history` records operation events. User-specific recovery evidence and before-images remain in `$XDG_STATE_HOME/setup-system/backups`.

## Receipts and backups

A native flock serializes package changes, Apply and Restore. Preview optional removals separately. Cleanup rechecks the native solver result and invokes dpkg on the exact approved names.

| Managed object | Recovery evidence |
| --- | --- |
| Existing user configuration | Before bytes, owner and mode, displaced inode and verified after state |
| Newly created user file | Recorded absence before Apply and the expected published file |
| Later manual edit, mode or ownership change | Restore reports a conflict and preserves the competing version |
| Previously restored file | Repeating Restore uses the recorded operation and checks the live state again |
| Login or boot files | Stage-specific backups under `/var/lib/setup-system` |
| Debian packages | Native APT and dpkg status and logs |

Managed-file publication retains its recovery index in `$XDG_STATE_HOME/setup-system/workstation.json`. The SQLite ledger records plugin ownership. File journals and original bytes are under `$XDG_STATE_HOME/setup-system/backups/`.

When the system task replaces the managed Firefox
policy, it retains the original inode in a private sibling directory named
`.setup-system-<filename>-<suffix>/original`. An administrator who had the old
file open can still write to that inode. After a conflict or interruption, compare
the live file, `original` and the content-addressed backup before recovering the
intended bytes and permissions. A new file at the live path is preserved.

## ♻️ Restore user files

For current operations, open **Managed resources** and choose **Roll back a file operation**. For earlier backups, choose **Restore a previous backup** on the welcome screen. The preview lists
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

## 📦 Recover a package transaction

System recovery depends on the stage that completed. Keep original release
artifacts. Use `dpkg --audit` to inspect an interrupted package operation and
review APT's proposed resolution before authorizing repair.

Open **Help and recovery**, then choose **Recovery** to see the last recorded stage and status from a recognized pre-plugin package journal. The original journal stays on disk. New package transactions use native package status and logs.

## 🖥️ Recover graphical login

The graphical login plan records the current systemd default target, the readable
GRUB inputs and the intended login files before confirmation. Apply checks them
again using the installed system task. The plan names any inputs that only
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

## 🔑 Recover a boot image

For Plymouth, inspect `/var/lib/setup-system/boot/unlock-*/operation.json`.
New entries record the verified mode in `after_mode` alongside the after-hash.
The journal names private directories on each destination filesystem in `apply_area`
and `restore_area`. Apply verifies the displaced inode before publishing the
prepared file and leaves a competing new name in place. Restore checks the
published inode, contents and mode. Inspect the recorded areas for `prepared`
and `displaced` files after a conflict or interruption. Retain the original
initramfs and these directories until normal and recovery boots have succeeded.
Historical entries can omit these fields. Confirm their mode independently. Follow the [graphical unlock recovery procedure](graphical_unlock.md).

When an older journal is migrated in place, its original bytes are saved in the adjacent `.v0.json` file. The `.v0.retained-*` entry keeps the original inode so later writes through an open editor remain recoverable. If migration reports a concurrent edit, inspect the current file and the retained or conflict files before retrying.

## 🧹 Resume a partial cleanup

Open **Managed resources → Check interrupted operations** to inspect prepared changes. If an administrative inspection requires a fresh authorization, review its resources and authorize the readback. A lost connection to a mutating action never triggers an automatic retry. **Review remaining cleanup** verifies completed deletions and offers a new confirmation for the remaining owned resources. Full cleanup restores successive managed-file revisions in reverse order. A local edit that changes the next expected fingerprint stops recovery.

After interrupted extension retirement, use **Remove extension** to resume removal of the recorded files. New or modified files require review. **Export history** writes a private JSONL report containing the same bounded, allowlisted events shown in global history.
