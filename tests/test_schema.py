import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine as sync_engine
from sqlalchemy.ext.asyncio import AsyncEngine

from setup_system.configuration.settings import Settings
from setup_system.infrastructure.database.engine import create_engine, prepare_file
from setup_system.infrastructure.database.migrations import (
    BASELINE,
    HEAD,
    V10,
    config,
    migrate,
    runtime_schema,
)


@pytest.mark.asyncio
async def test_schema_lease(engine: AsyncEngine) -> None:
    async with runtime_schema(engine):
        with pytest.raises(ValueError, match="in use"):
            await migrate(engine)
    async with engine.connect() as connection:
        assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 1


@pytest.mark.asyncio
async def test_historical_ledger(home: Path) -> None:
    engine = create_engine(Settings())
    prepare_file(engine)
    path = Path(engine.url.database or "")
    # Use the immutable historical schema, as created by the original writer.
    with closing(sqlite3.connect(path)) as db:
        temp = sync_engine("sqlite://")
        try:
            with temp.connect() as connection:
                revision = ScriptDirectory.from_config(config(connection)).get_revision(BASELINE)
                assert revision
                sql: str = revision.module.SCHEMA
        finally:
            temp.dispose()
        db.executescript(sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
        db.execute(
            "INSERT INTO operations(id,plugin,action,release,phase) "
            "VALUES('o','sample','save','old','unresolved')"
        )
        db.execute("INSERT INTO confirmations VALUES('o','{}','keep')")
        db.execute("INSERT INTO reservations VALUES('file','a','o')")
        db.commit()
    try:
        with pytest.raises(ValueError, match="migrate"):
            async with runtime_schema(engine):
                pytest.fail("Unexpected legacy runtime")
        saved = await migrate(engine)
        assert saved and saved.is_file()
        with closing(sqlite3.connect(saved)) as db:
            assert db.execute("SELECT recovery FROM confirmations").fetchone() == ("keep",)
            assert not db.execute(
                "SELECT 1 FROM sqlite_schema WHERE name='operation_steps'"
            ).fetchone()
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (HEAD,)
            assert db.execute("SELECT * FROM reservations").fetchone() == ("file", "a", "o")
            assert db.execute("SELECT * FROM operation_steps").fetchall() == []
            assert db.execute("SELECT phase FROM operations").fetchone() == ("unresolved",)
            assert db.execute(
                "SELECT sql FROM sqlite_schema WHERE name='unfinished_action'"
            ).fetchone()
        assert await migrate(engine) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_schema_stays_intact(home: Path) -> None:
    engine = create_engine(Settings())
    prepare_file(engine)
    path = Path(engine.url.database or "")
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE operations(id INTEGER)")
    before = path.read_bytes()
    try:
        with pytest.raises(ValueError, match="Unrecognized"):
            await migrate(engine)
        assert path.read_bytes() == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_versioned_operational_store(home: Path) -> None:
    engine = create_engine(Settings())
    prepare_file(engine)
    path = Path(engine.url.database or "")
    db_engine = sync_engine("sqlite:///" + str(path))
    try:
        with db_engine.begin() as conn:
            command.upgrade(config(conn, v10=True), V10)
            conn.exec_driver_sql("INSERT INTO extensions VALUES ('sample')")
            conn.exec_driver_sql(
                "INSERT INTO extension_releases VALUES (1,'sample',?, '2','source',1,1)",
                ("0" * 64,),
            )
            conn.exec_driver_sql(
                "INSERT INTO operations(seq,operation_id,release_id,action_id,status,"
                "settings_revision,inputs,settings,"
                "plan_summary,plan_outcome) "
                "VALUES(1,?,1,'save','running','rev','{}','{}','summary','ready')",
                ("a" * 32,),
            )
            conn.exec_driver_sql("INSERT INTO active_operations VALUES('sample',1)")
        backup = await migrate(engine)
        assert backup
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT phase FROM operations").fetchone() == ("unresolved",)
            assert db.execute("SELECT action_id FROM v10_operations").fetchone() == ("save",)
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT count(*) FROM operation_steps").fetchone() == (0,)
    finally:
        db_engine.dispose()
        await engine.dispose()
