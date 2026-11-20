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
- `$XDG_STATE_HOME/setup-system/backups/`: private user backups with before/after ownership evidence.
- `$XDG_STATE_HOME/setup-system/workstation.json`: existing managed desktop file ownership.
  New user roles share this same ownership receipt so theme/base repeats remain compatible.

Keep original source/runtime releases. Delivery rejects a changed existing release rather
than overwriting it. Native H02 metadata, hashes and source identity remain required.
The system executor imports a newly verified root-owned source copy, never the user's
editable release tree. No authentication store is part of an application backup.

Open `setup-system`, use Workspace Tools and review the backup ID before restoring.
Existing files changed since the operation require manual review. Package database
repair belongs to Debian administration on the selected target: inspect `dpkg --audit`
and APT's proposed resolution before authorizing any repair. No automatic purge/autoremove.

For a failed graphical login, retain SSH or Ctrl+Alt+F3. Inspect greetd's journal and
restore the backed-up managed login/GRUB files only after checking their receipts.
GRUB remains visible; native disk/HOME unlock remains available. The graphical unlock
path needs the [separate boot preflight](graphical_unlock.md).
