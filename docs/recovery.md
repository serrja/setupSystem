# Recovery and verification

## Installation stages

Run the same verified `sh install.sh --release vX.Y.Z` delivery command again after
fixing the reported failure; direct root login also requires `--user NAME`. The installer
rechecks the platform, account, package resolution and current files rather than
trusting a stale success flag. The root-owned `/var/lib/setup-system/install.json`
records the actual failed stage and completed system stages. The delivery script
prepares pinned user Python/uv; Debian/APT, CA trust and initial network access remain
prerequisites. An existing encrypted HOME must already be unlocked before user files
can be prepared. Do not delete HOME or recursively change its ownership.

APT refresh, package installation, persistent backports source, user runtime/files
and final `system-desktop` integration are separate stages. A failed user stage leaves the installed packages in place and
returns a nonzero exit code. There is no transactional APT rollback. Offline,
signature, unavailable revision or lock failures need their underlying cause fixed
before the same command can continue. Never bypass authentication or agree to package
removals to make this profile pass.

Package starts are deferred while installation runs so the current network/session
can stay active. An unknown existing `policy-rc.d` requires review before package
mutation; it is not overwritten. An interrupted setupSystem-owned temporary policy
is recognized and removed after a successful retry. After a power loss or a forcibly
terminated dpkg operation, inspect `sudo dpkg --audit`; review any pending package
configuration before running `sudo dpkg --configure --pending` and retrying setup.
Do not remove another process's APT/dpkg lock files.

Keep the verified source bundle and its lockfile unchanged when recovering runtime
preparation. Allocated release versions are reconciled only in private temporary
build files under the target user's cache; all dependency versions and hashes stay
locked. The temporary copy is removed when preparation returns, including handled
failures. Fix the reported error and rerun the same entrypoint instead of regenerating
the lockfile or installing Python packages as root.

## Managed user files

The user runtime CLI is installed at
`$XDG_DATA_HOME/setup-system/runtime/bin/setup-system`, normally:

```bash
"$HOME/.local/share/setup-system/runtime/bin/setup-system" workstation restore BACKUP_ID
```

This previews the profile restore. Add `--apply` only after reviewing it.
The installer prints the backup ID; private backup manifests, hashes and modes live
under `$XDG_STATE_HOME/setup-system/backups`. Restore refuses later user edits and
unsafe paths. Keep backups private. A repeated unchanged apply reuses its receipt
and does not create duplicate backups.

Unknown Hyprland entrypoints, edited managed files, an unmanaged runtime directory or
a conflicting local uv binary stop instead of being overwritten. Preserve the file,
compare it with the intended assets, then decide how to reconcile it. User overrides
belong in the documented Hyprland local override, which is never managed by setup.

The general configuration tools remain available: `setup-system backup`,
`setup-system restore --backup ID --dry-run` and the scoped workstation restore
above. General restore's `--apply --yes` restores absent files while preserving
conflicts; `--replaceCurrent` backs up a reviewed conflict before replacing it.
A live vault SQLite database requires the application's own backup mechanism.

## Session checks

Installation does not start Hyprland, log out the user or reboot. Graphical login is
enabled for the next normal boot; authenticate normally to start the managed session.
If graphical login fails, switch with Ctrl+Alt+F3, log in locally and use the exact
TTY launcher printed by setup. Super+F9 → Logout returns to graphical login.
See [login and boot recovery](desktop_login.md#recovery) before changing managed
greetd or GRUB files. See [Hyprland](hyprland.md) for fallback rendering/scaling and the
manual login/audio/portal/lock/resume checklist. A successful package or fixture test
is not a successful graphical-session check.

[Workspace recovery](workspace_guide.md), [terminal configuration](terminal_guide.md)
and [application tools](../README.md#tools-and-development) retain their own
scope. The Hyprland installer does not install Docker, move project checkouts, copy
credentials, configure Codex or change the login shell.
