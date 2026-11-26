# 🗄️ Operation database

setupSystem v10 stores its operation history in `$XDG_STATE_HOME/setup-system/v10/state.sqlite3`. Without XDG overrides this resolves to `~/.local/state/setup-system/v10/state.sqlite3`. Set `SETUP_SYSTEM_DATABASE_URL` to an absolute `sqlite+aiosqlite` file URL under the current user's private HOME to select another location.

SQLAlchemy maps releases, operations, resource identities, reservations, previous holdings, consumer relationships, observations, journal events and restore records. A transaction records the confirmed plan and reserves the affected identities. The host commits before starting native work. A later transaction records the readback. Uncertain results retain evidence for inspection. Rolling back SQL affects database changes only.

Every connection enforces foreign keys. SQLite's writer lock serializes write decisions with a bounded busy timeout. The default rollback journal suits this local, short-transaction workload. The database uses its own Alembic version table to identify compatible storage.

## Migrations

Run database commands as the application user from a prepared checkout or with `make -C /usr/lib/setup-system` for the Debian installation.

```sh
make db-upgrade
make db-current
make db-check
```

Startup upgrades the schema before opening repositories. The migration runner shares the connection with Alembic and acquires a write transaction before inspecting schema identity. Developers can generate a candidate revision from a disposable current database with `make db-revision`. Review the generated DDL and commit it with its model changes.

Downgrade belongs to a separate developer recovery procedure. Stop all application processes and copy the entire private state directory first. The initial revision refuses downgrade once registrations or operation evidence exist. For an empty disposable store, set the isolated database URL and run `make db-downgrade-empty`. This removes its tables. Keep the original database for restoring evidence. [Recovery](recovery.md)

## Earlier installations

Before upgrading, close setupSystem and preserve the complete prior state, data and configuration directories with their permissions. The older `$XDG_STATE_HOME/setup-system/resources.sqlite3`, history, backup files, extension generations and private settings remain available for manual inspection. Files without new, verified ownership are treated as preexisting. Resolve pending actions using the original evidence before planning changes with v10.
