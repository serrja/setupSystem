# Configuration

Desired settings use schema version 1. Unknown fields, unsupported versions and secret
fields are rejected. Version-zero TOML without `schema_version` migrates in memory;
only an explicit save writes it. Machine facts are inspected separately.

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
