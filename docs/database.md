# 🗄️ Database

The host uses one SQLite database at the configured local path. Its schema tracks confirmations, step receipts, changes, holdings, reservations, dependency edges and recovery records. The application initializes an empty database on first use.

## 🗄️ Schema upgrades

Close running instances and run the migration as the account that owns the database.

```sh
setup-system migrate
```

The migration takes an exclusive schema lease and creates a SQLite backup of an existing database. It checks an unversioned historical schema before stamping its baseline. A running instance holds a shared lease until shutdown. Worker execution and user confirmation take place outside database transactions.

Migration preserves historical evidence. A historical operation without step records requires its original executor or independent readback before recovery. Empty step history provides no proof of completion.

When only the historical `$XDG_STATE_HOME/setup-system/resources.sqlite3` exists, the default configuration selects it for migration. If both stores exist, select the intended database explicitly with `SETUP_SYSTEM_DATABASE_URL`.

## 🔄 Recovery

A `UnitOfWork` opens an `AsyncSession` for each read or atomic write. Operation, resource and dependency repositories share the session for a transition. The writer owns the commit. Worker calls and user confirmation happen after the transaction closes.

Revision `1200cafe0003` records historical approvals and reconciliation evidence. The v10 importer retains source observations and previous holdings. A partial outcome or unfinished restore keeps the historical operation unresolved. Recovery compares historical identity and independent readback with those records. Conflicting evidence keeps the operation unresolved. Existing databases require an explicit upgrade and keep their backup. [Recovery procedure](recovery.md)
