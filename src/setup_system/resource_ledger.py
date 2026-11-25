"""Durable operation and ownership records, independent of diagnostic output."""

import asyncio
import fcntl
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID

import aiosqlite

from setup_system.infrastructure.user_storage import private_directory, safe_file
from setup_system_sdk import Inputs, Outcome, Plan, Resource, Result

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations(
    id TEXT PRIMARY KEY, plugin TEXT NOT NULL, action TEXT NOT NULL, release TEXT NOT NULL,
    phase TEXT NOT NULL, outcome TEXT, started TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS unfinished_action ON operations(plugin,action)
WHERE phase IN ('prepared','applied','unresolved');
CREATE TABLE IF NOT EXISTS confirmations(
    operation TEXT PRIMARY KEY REFERENCES operations(id), snapshot TEXT NOT NULL, recovery TEXT
);
CREATE TABLE IF NOT EXISTS changes(
    operation TEXT NOT NULL REFERENCES operations(id), kind TEXT NOT NULL, identity TEXT NOT NULL,
    phase TEXT NOT NULL, resource TEXT NOT NULL, PRIMARY KEY(operation, kind, identity)
);
CREATE TABLE IF NOT EXISTS native_readbacks(
    operation TEXT NOT NULL REFERENCES operations(id), kind TEXT NOT NULL, identity TEXT NOT NULL,
    resource TEXT NOT NULL, PRIMARY KEY(operation,kind,identity)
);
CREATE TABLE IF NOT EXISTS holdings(
    kind TEXT NOT NULL, identity TEXT NOT NULL, plugin TEXT NOT NULL, resource TEXT NOT NULL,
    PRIMARY KEY(kind, identity, plugin)
);
CREATE TABLE IF NOT EXISTS requirements(consumer TEXT NOT NULL, provider TEXT NOT NULL,
    PRIMARY KEY(consumer,provider));
CREATE TABLE IF NOT EXISTS planned_requirements(
    operation TEXT REFERENCES operations(id), consumer TEXT NOT NULL,provider TEXT NOT NULL,
    PRIMARY KEY(operation,consumer,provider)
);
CREATE TABLE IF NOT EXISTS rollbacks(
    operation TEXT PRIMARY KEY REFERENCES operations(id), phase TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations(
    kind TEXT NOT NULL, identity TEXT NOT NULL, operation TEXT NOT NULL REFERENCES operations(id),
    PRIMARY KEY(kind, identity)
);
"""


class LocalSnapshot(Inputs):
    local: bool = True
    operation_id: UUID
    action: str
    resource: Resource


_owner: ContextVar[tuple[Path, str] | None] = ContextVar("plugin_writer", default=None)


class ResourceLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    @asynccontextmanager
    async def writer(self, plugin: str) -> AsyncIterator[None]:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", plugin):
            raise ValueError("Invalid lock namespace")
        key = (self.path, plugin)
        if _owner.get() == key:
            yield
            return

        def acquire() -> int:
            with private_directory(self.path.parent / "locks" / plugin) as directory:
                fd = os.dup(directory)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    raise ValueError("An operation is active in another session") from None
                return fd

        opening = asyncio.create_task(asyncio.to_thread(acquire))
        try:
            fd = await asyncio.shield(opening)
        except asyncio.CancelledError:
            os.close(await opening)
            raise
        token = _owner.set(key)
        try:
            yield
        finally:
            _owner.reset(token)
            os.close(fd)

    def _prepare(self) -> None:
        with private_directory(self.path.parent) as directory:
            safe_file(self.path, os.getuid())
            fd = os.open(
                self.path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            os.close(fd)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        await asyncio.to_thread(self._prepare)
        async with aiosqlite.connect(self.path, timeout=5) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(_SCHEMA)
            await db.execute("BEGIN IMMEDIATE")
            yield db
            await db.commit()

    async def prepare(
        self,
        operation: UUID,
        plugin: str,
        action: str,
        digest: str,
        plan: Plan,
        *,
        remove_preexisting: bool = False,
        dependencies: tuple[str, ...] = (),
        snapshot: str | None = None,
    ) -> None:
        """Reserve the exact operation and all identities before any native mutation."""
        key = str(operation)
        async with self.connection() as db:
            await db.execute(
                "INSERT INTO operations(id,plugin,action,release,phase) VALUES(?,?,?,?,'planned')",
                (key, plugin, action, digest),
            )
            if (
                snapshot is None
                and action in ("save-settings", "remove-profile", "save-secret", "remove-secret")
                and len(plan.resources) == 1
            ):
                snapshot = LocalSnapshot(
                    operation_id=operation, action=action, resource=plan.resources[0]
                ).model_dump_json()
            if snapshot:
                await db.execute(
                    "INSERT INTO confirmations(operation,snapshot) VALUES(?,?)", (key, snapshot)
                )
            if action == "uninstall":
                async with db.execute(
                    "SELECT consumer FROM requirements WHERE provider=? UNION SELECT "
                    "consumer FROM planned_requirements WHERE provider=?",
                    (plugin, plugin),
                ) as cursor:
                    if await cursor.fetchone():
                        raise ValueError("Another installed program requires this plugin")
            if dependencies and action != "uninstall":
                for provider in dependencies:
                    async with db.execute(
                        "SELECT 1 FROM operations WHERE plugin=? AND action='uninstall' "
                        "AND phase IN ('prepared','applied','unresolved')",
                        (provider,),
                    ) as cursor:
                        if await cursor.fetchone():
                            raise ValueError("Dependency removal is in progress")
                    await db.execute(
                        "INSERT OR IGNORE INTO planned_requirements VALUES(?,?,?)",
                        (key, plugin, provider),
                    )
            for resource in plan.resources:
                args = (resource.kind, resource.identity)
                async with db.execute(
                    "SELECT plugin,resource FROM holdings WHERE kind=? AND identity=?", args
                ) as cursor:
                    owners = {
                        name: Resource.model_validate_json(raw)
                        for name, raw in await cursor.fetchall()
                    }
                own = owners.get(plugin)
                others = owners.keys() - {plugin}
                if resource.effect == "remove":
                    if others or (own and set(own.consumers) - {plugin}):
                        raise ValueError("Another plugin needs the selected resource")
                    if not remove_preexisting and (
                        not own
                        or not own.cleanup
                        or own.ownership != "created"
                        or not own.after
                        or own.after != resource.before
                        or own.remote_id != resource.remote_id
                    ):
                        raise ValueError(
                            "Removal requires verified ownership and an unchanged fingerprint"
                        )
                elif (
                    own
                    and own.after
                    and resource.before != own.after
                    and resource.kind != "plugin_settings"
                ):
                    raise ValueError("The managed resource differs from its verified fingerprint")
                elif others and (
                    resource.ownership not in ("shared", "preexisting")
                    or resource.before != resource.after
                    or any(owners[name].after != resource.before for name in others)
                ):
                    raise ValueError("Another plugin manages the selected resource")
                await db.execute("INSERT INTO reservations VALUES(?,?,?)", (*args, key))
                await db.execute(
                    "INSERT INTO changes VALUES(?,?,?,'prepared',?)",
                    (key, *args, resource.model_dump_json()),
                )
            await db.execute("UPDATE operations SET phase='prepared' WHERE id=?", (key,))

    async def applied(
        self, operation: UUID, recovery: str | None = None, observations: tuple[Resource, ...] = ()
    ) -> None:
        async with self.connection() as db:
            await db.executemany(
                "INSERT INTO native_readbacks VALUES (?,?,?,?)",
                [
                    (str(operation), r.kind, r.identity, r.model_dump_json())
                    for r in observations
                    if r.verified
                ],
            )
            if recovery:
                await db.execute(
                    "UPDATE confirmations SET recovery=? WHERE operation=?",
                    (recovery, str(operation)),
                )
            await db.execute(
                "UPDATE operations SET phase='applied' WHERE id=? AND phase='prepared'",
                (str(operation),),
            )
            await db.execute(
                "UPDATE changes SET phase='applied' WHERE operation=?", (str(operation),)
            )

    async def finish(self, operation: UUID, result: Result) -> None:
        """Verify readback and retain uncertain reservations."""
        key = str(operation)
        async with self.connection() as db:
            async with db.execute(
                "SELECT plugin,phase FROM operations WHERE id=?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row or row[1] not in ("prepared", "applied", "unresolved"):
                raise ValueError("Operation is already final or unknown")
            plugin = row[0]
            async with db.execute(
                "SELECT kind,identity,resource FROM changes WHERE operation=?", (key,)
            ) as cursor:
                planned = {
                    (kind, name): Resource.model_validate_json(raw)
                    for kind, name, raw in await cursor.fetchall()
                }
            verified = {(item.kind, item.identity): item for item in result.resources}
            success = result.outcome in (
                Outcome.CHANGED,
                Outcome.COMPLETE,
                Outcome.NO_CHANGES,
                Outcome.NEEDS_REBOOT,
            )
            if verified.keys() - planned.keys() or (success and verified.keys() != planned.keys()):
                raise ValueError("Readback does not cover the prepared resource identities")
            if not success:
                verified = {key: item for key, item in verified.items() if item.verified}
            async with db.execute(
                "SELECT kind,identity,resource FROM native_readbacks WHERE operation=?", (key,)
            ) as cursor:
                native = {
                    (kind, name): Resource.model_validate_json(raw)
                    for kind, name, raw in await cursor.fetchall()
                }
            async with db.execute(
                "SELECT kind,identity FROM changes WHERE operation=? AND phase='verified'", (key,)
            ) as cursor:
                completed = set(await cursor.fetchall())
            for identity, observation in verified.items():
                captured = native.get(identity)
                if captured and any(
                    value is not None and value != actual
                    for value, actual in (
                        (captured.after, observation.after),
                        (captured.permissions, observation.permissions),
                        (captured.remote_id, observation.remote_id),
                    )
                ):
                    raise ValueError("Resource identity changed after its native publication")
                if identity in completed:
                    continue
                desired = planned[identity]
                if desired.after is not None and observation.after != desired.after:
                    raise ValueError("Readback differs from the confirmed resource")
                if (
                    observation.effect != desired.effect
                    or observation.ownership != desired.ownership
                ):
                    raise ValueError("Readback changed the approved resource contract")
                if desired.effect == "remove":
                    await db.execute(
                        "DELETE FROM holdings WHERE kind=? AND identity=? AND plugin=?",
                        (*identity, plugin),
                    )
                else:
                    async with db.execute(
                        "SELECT resource FROM holdings WHERE kind=? AND identity=? AND plugin=?",
                        (*identity, plugin),
                    ) as cursor:
                        previous = await cursor.fetchone()
                    if previous:
                        original = Resource.model_validate_json(previous[0])
                        desired = desired.model_copy(
                            update={
                                "ownership": original.ownership,
                                "before": original.before,
                                "cleanup": original.cleanup,
                                "source_sha256": desired.source_sha256 or original.source_sha256,
                            }
                        )
                    held = desired.model_copy(
                        update={"after": observation.after, "remote_id": observation.remote_id}
                    )
                    await db.execute(
                        "INSERT OR REPLACE INTO holdings VALUES(?,?,?,?)",
                        (*identity, plugin, held.model_dump_json()),
                    )
                await db.execute(
                    "UPDATE changes SET phase='verified',resource=? "
                    "WHERE operation=? AND kind=? AND identity=?",
                    (observation.model_dump_json(), key, *identity),
                )
                await db.execute(
                    "DELETE FROM reservations WHERE operation=? AND kind=? AND identity=?",
                    (key, *identity),
                )
            unresolved = result.outcome in (Outcome.PARTIAL, Outcome.UNKNOWN_OUTCOME) or (
                not success and row[1] == "applied"
            )
            if success:
                await db.execute(
                    "INSERT OR IGNORE INTO requirements SELECT consumer,provider FROM "
                    "planned_requirements WHERE operation=?",
                    (key,),
                )
                await db.execute(
                    "DELETE FROM requirements WHERE consumer=? AND EXISTS "
                    "(SELECT 1 FROM operations WHERE id=? AND action='uninstall')",
                    (plugin, key),
                )
            if result.recovery_id:
                await db.execute(
                    "UPDATE confirmations SET recovery=? WHERE operation=?",
                    (result.recovery_id, key),
                )
            if not unresolved:
                await db.execute("DELETE FROM planned_requirements WHERE operation=?", (key,))
                await db.execute("DELETE FROM reservations WHERE operation=?", (key,))
            await db.execute(
                "UPDATE operations SET phase=?, outcome=? WHERE id=?",
                ("unresolved" if unresolved else "verified", result.outcome.value, key),
            )

    async def removable_reservations(self, operation: UUID, plugin: str) -> set[tuple[str, str]]:
        async with (
            self.connection() as db,
            db.execute(
                "SELECT h.resource,c.resource FROM changes c JOIN reservations r USING "
                "(operation,kind,identity) "
                "JOIN holdings h USING (kind,identity) WHERE c.operation=? AND h.plugin=? "
                "AND c.phase!='verified' "
                "AND NOT EXISTS (SELECT 1 FROM holdings other WHERE other.kind=h.kind AND "
                "other.identity=h.identity AND other.plugin!=h.plugin)",
                (str(operation), plugin),
            ) as cursor,
        ):
            permitted = set()
            for held, planned in await cursor.fetchall():
                owner = Resource.model_validate_json(held)
                target = Resource.model_validate_json(planned)
                if (
                    owner.ownership == "created"
                    and owner.cleanup
                    and not set(owner.consumers) - {plugin}
                    and owner.after == target.before
                    and owner.remote_id == target.remote_id
                ):
                    permitted.add((owner.kind, owner.identity))
            return permitted

    async def holdings(self, plugin: str) -> list[Resource]:
        if not await asyncio.to_thread(self.path.exists):
            return []
        async with (
            self.connection() as db,
            db.execute(
                "SELECT resource FROM holdings WHERE plugin=? ORDER BY kind,identity", (plugin,)
            ) as cursor,
        ):
            return [Resource.model_validate_json(row[0]) for row in await cursor.fetchall()]

    async def pending(self, plugin: str) -> list[str]:
        if not await asyncio.to_thread(self.path.exists):
            return []
        async with (
            self.connection() as db,
            db.execute(
                "SELECT c.snapshot FROM confirmations c JOIN operations o ON o.id=c.operation "
                "WHERE o.plugin=? AND o.phase IN ('prepared','applied','unresolved') "
                "ORDER BY o.started",
                (plugin,),
            ) as cursor,
        ):
            return [row[0] for row in await cursor.fetchall()]

    async def recoveries(self, plugin: str) -> list[tuple[str, str]]:
        if not await asyncio.to_thread(self.path.exists):
            return []
        async with (
            self.connection() as db,
            db.execute(
                "SELECT o.id,c.recovery FROM operations o JOIN confirmations c ON o.id=c.operation "
                "LEFT JOIN rollbacks r ON o.id=r.operation WHERE o.plugin=? "
                "AND c.recovery IS NOT NULL "
                "AND (r.phase IS NULL OR r.phase!='verified') ORDER BY o.rowid DESC",
                (plugin,),
            ) as cursor,
        ):
            return [(row[0], row[1]) for row in await cursor.fetchall()]

    async def reserve_rollback(
        self, plugin: str, operation: str, receipt: str, paths: set[str]
    ) -> None:
        async with self.connection() as db:
            async with db.execute(
                "SELECT c.recovery FROM confirmations c JOIN operations o ON o.id=c.operation "
                "WHERE o.id=? AND o.plugin=?",
                (operation, plugin),
            ) as cursor:
                if await cursor.fetchone() != (receipt,):
                    raise ValueError("The receipt does not belong to this operation")
            async with db.execute(
                "SELECT kind,identity FROM changes WHERE operation=?", (operation,)
            ) as cursor:
                identities = await cursor.fetchall()
            selected = [(k, n) for k, n in identities if n in paths]
            if paths - {name for _, name in selected}:
                raise ValueError("Receipt contains paths outside the confirmed operation")
            for kind, identity in selected:
                async with db.execute(
                    "SELECT 1 FROM holdings WHERE kind=? AND identity=? AND plugin!=? "
                    "UNION ALL SELECT 1 FROM changes c JOIN operations o ON c.operation=o.id "
                    "LEFT JOIN rollbacks r ON r.operation=o.id WHERE c.kind=? AND c.identity=? "
                    "AND o.rowid>(SELECT rowid FROM operations WHERE id=?) "
                    "AND (r.phase IS NULL OR r.phase!='verified') AND c.phase='verified'",
                    (kind, identity, plugin, kind, identity, operation),
                ) as cursor:
                    if await cursor.fetchone():
                        raise ValueError("A newer operation or another plugin holds this resource")
                async with db.execute(
                    "SELECT operation FROM reservations WHERE kind=? AND identity=?",
                    (kind, identity),
                ) as found:
                    existing = await found.fetchone()
                if existing is None:
                    await db.execute(
                        "INSERT INTO reservations VALUES(?,?,?)", (kind, identity, operation)
                    )
                elif existing != (operation,):
                    raise ValueError("Resource has an unfinished operation")
            await db.execute("INSERT OR IGNORE INTO rollbacks VALUES(?,'prepared')", (operation,))

    async def finish_rollback(self, plugin: str, operation: str) -> None:
        async with self.connection() as db:
            async with db.execute(
                "SELECT snapshot FROM confirmations WHERE operation=?", (operation,)
            ) as cursor:
                record = await cursor.fetchone()
            if not record:
                raise ValueError("Missing original confirmation")
            from setup_system.plugin_manager import Snapshot

            source = Snapshot.model_validate_json(record[0])
            before = {(r.kind, r.identity): r for r in source.mutation.holdings}
            async with db.execute(
                "SELECT kind,identity FROM reservations WHERE operation=?", (operation,)
            ) as cursor:
                selected = set(await cursor.fetchall())
            for resource in source.mutation.plan.resources:
                if (resource.kind, resource.identity) not in selected:
                    continue
                args = (resource.kind, resource.identity, plugin)
                original = before.get((resource.kind, resource.identity))
                if original is None:
                    await db.execute(
                        "DELETE FROM holdings WHERE kind=? AND identity=? AND plugin=?", args
                    )
                else:
                    await db.execute(
                        "INSERT OR REPLACE INTO holdings VALUES(?,?,?,?)",
                        (*args, original.model_dump_json()),
                    )
            await db.execute(
                "UPDATE rollbacks SET phase='verified' WHERE operation=?", (operation,)
            )
            await db.execute("DELETE FROM reservations WHERE operation=?", (operation,))

    async def registered_receipts(self) -> set[str]:
        if not await asyncio.to_thread(self.path.exists):
            return set()
        async with (
            self.connection() as db,
            db.execute("SELECT recovery FROM confirmations WHERE recovery IS NOT NULL") as cursor,
        ):
            return {r[0] for r in await cursor.fetchall()}

    async def dependents(self, plugin: str) -> list[str]:
        if not await asyncio.to_thread(self.path.exists):
            return []
        async with (
            self.connection() as db,
            db.execute(
                "SELECT consumer FROM requirements WHERE provider=? UNION SELECT consumer "
                "FROM planned_requirements WHERE provider=? ORDER BY consumer",
                (plugin, plugin),
            ) as cursor,
        ):
            return [row[0] for row in await cursor.fetchall()]
