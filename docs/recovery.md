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
Readback marks each verified write. A failed role can leave a partial operation.
Review the affected files before retrying or restoring. Legacy file journals are
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
