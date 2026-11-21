# Configuration

User settings schema 2 stores selected catalog IDs, theme, display layout and cleanup choices. Reading version 1 maps its application switches into the new selection in memory. Save intentions to persist the migration with a recoverable copy of the original. System settings retain schema 1. Unknown versions or fields require review.

Explicit TOML fields take precedence. A missing field can use a matching
`SETUP_SYSTEM_` environment variable, such as `SETUP_SYSTEM_KEYBOARD=us`.
Built-in defaults apply when both are absent.

| Location | Ownership / purpose |
| --- | --- |
| `/etc/setup-system/system.toml` | Root-owned applied system desired state |
| `$XDG_CONFIG_HOME/setup-system/settings.toml` | User desired applications and theme |
| `$XDG_CONFIG_HOME/setup-system/system-intentions.toml` | Unprivileged draft, not applied system state |
| `/var/lib/setup-system/` | System receipts, verified source and recovery backups |
| `$XDG_STATE_HOME/setup-system/` | User receipts, backups and release identity |
| `$XDG_CACHE_HOME/setup-system/` | Verified user downloads and runtime cache |

XDG defaults follow platformdirs. Paths must stay within the verified user HOME where
applicable. Symlinks, foreign ownership, special files and writable-by-others settings
are rejected. Saves are atomic with a content fingerprint and backup. Reload after a
conflict; do not overwrite an unreviewed file. System changes use the fixed executor,
never a writable TOML command or playbook path.

Passwords, authentication tokens, private application databases and subscription URLs
are not settings. Let sudo and each application's native account UI manage them.

A previous installation can have a private `/etc/setup-system` directory. If Base system reports that it cannot read `system.toml`, an administrator should inspect the directory, its ownership and access controls on the target. The app needs traversal of that directory and read access to the system intentions file. Where the files are reviewed as shareable, grant traversal on **that directory only** and read access on **that file only** to the intended user, for example with a per-user POSIX ACL. Preserve private sibling files and avoid recursive permission changes. Workspace operations remain available while Base settings need review. The Firefox action can migrate an exact earlier managed policy in otherwise empty, private policy directories. It preserves unknown files for administrator review.
