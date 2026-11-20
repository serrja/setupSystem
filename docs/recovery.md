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
- `/var/lib/setup-system/settings-backups/`: previous system desired settings by content hash.
- `$XDG_STATE_HOME/setup-system/workstation.json` holds the user ownership index.
- `$XDG_STATE_HOME/setup-system/backups/` holds private file operation journals and original bytes.

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
displaced file available for inspection. Legacy file journals are
converted on explicit recovery when their before/after evidence is sufficient.
Original journals are retained. Unknown schemas and ambiguous legacy ownership
require manual review. Private application databases use their application's
native recovery procedure.

System recovery depends on the stage that completed. Keep original release
artifacts. Use `dpkg --audit` to inspect an interrupted package operation and
review APT's proposed resolution before authorizing repair.

For a failed graphical login, retain SSH or Ctrl+Alt+F3. Inspect greetd's journal and
restore the backed-up managed login/GRUB files only after checking their receipts.
GRUB remains visible; native disk/HOME unlock remains available. The graphical unlock
path needs the [separate boot preflight](graphical_unlock.md).
