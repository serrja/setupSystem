"""Validate schema identity and migrate a private database under a process lease."""

from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import stat
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, closing, contextmanager
from functools import lru_cache
from importlib import resources
from pathlib import Path
from uuid import uuid4

import anyio
from alembic import command
from alembic.config import Config
from setup_system_sdk.files.user_storage import private_directory, safe_file
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from setup_system.infrastructure.database.engine import prepare_file

BASELINE = "1200cafe0001"
HEAD = "1200cafe0002"
V10 = "1000a10c0001"


def script_location() -> Path:
    package = resources.files("setup_system").joinpath("_migrations")
    if package.is_dir():
        return Path(str(package))
    source = Path(__file__).resolve().parents[4] / "migrations"
    if not (source / "env.py").is_file():
        raise RuntimeError("The installed migration history is missing")
    return source


def config(connection: Connection, *, v10: bool = False) -> Config:
    cfg = Config()
    cfg.set_main_option("path_separator", "os")
    location = script_location()
    cfg.set_main_option("script_location", str(location).replace("%", "%%"))
    if v10:
        cfg.set_main_option("version_locations", str(location / "v10").replace("%", "%%"))
        cfg.attributes["version_table"] = "setup_system_v10_revision"
    cfg.attributes["connection"] = connection
    return cfg


def schema(connection: Connection) -> dict[str, tuple[str, ...]]:
    # Preserve quoted literals and operators when comparing constraints and partial indexes.
    rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
    )
    return {
        name: tuple(re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\w+|[^\s]", sql))
        for name, sql in rows
    }


@lru_cache(maxsize=3)
def reference(revision: str) -> dict[str, tuple[str, ...]]:
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            command.upgrade(config(connection, v10=revision == V10), revision)
            result = schema(connection)
            if revision == BASELINE:
                del result["alembic_version"]
            return result
    finally:
        engine.dispose()


def backup(path: Path) -> Path:
    destination = path.with_name(f"{path.name}.{uuid4().hex}.bak")
    with private_directory(path.parent) as parent:
        fd = os.open(
            destination.name,
            os.O_EXCL | os.O_NOFOLLOW | os.O_CREAT | os.O_WRONLY,
            0o600,
            dir_fd=parent,
        )
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(destination)) as copy:
                    source.backup(copy)
            os.fsync(fd)
            os.fsync(parent)
        except BaseException:
            destination.unlink()
            raise
        finally:
            os.close(fd)
    return destination


@contextmanager
def schema_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    lock = path.with_name(path.name + ".schema.lock")
    with private_directory(path.parent) as parent:
        safe_file(lock, os.getuid())
        fd = os.open(
            lock.name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent
        )
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("Unsafe database lease file")
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("The database is in use by another runtime or migration") from None
        yield
    finally:
        os.close(fd)


def current(connection: Connection) -> str | None:
    if not connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='alembic_version'"
    ).first():
        return None
    rows = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars().all()
    if len(rows) != 1 or rows[0] not in {BASELINE, HEAD}:
        raise ValueError("Unrecognized database revision")
    return str(rows[0])


def validate_head(connection: Connection) -> None:
    expected = reference(HEAD)
    actual = schema(connection)
    if any(actual.get(name) != ddl for name, ddl in expected.items()):
        raise ValueError("The database differs from its recorded revision")
    extras = actual.keys() - expected.keys()
    if extras:
        if "setup_system_v10_revision" not in extras:
            raise ValueError("Unexpected objects in the operational database")
        source = set(reference(V10))
        source.remove("operations")
        if extras != source | {"v10_operations"}:
            raise ValueError("Unexpected objects in the converted database")


def _upgrade(connection: Connection, path: Path, *, initialize: bool) -> Path | None:
    objects = schema(connection)
    revision = current(connection)
    if revision == HEAD:
        validate_head(connection)
        return None
    if initialize and objects:
        raise ValueError("Run setup-system migrate before opening this database")
    legacy = False
    v10 = False
    if objects:
        if revision == BASELINE:
            expected = {k: v for k, v in objects.items() if k != "alembic_version"}
            if expected != reference(BASELINE):
                raise ValueError("The ledger differs from its recorded baseline")
        elif "setup_system_v10_revision" in objects:
            v10 = objects == reference(V10) and connection.exec_driver_sql(
                "SELECT version_num FROM setup_system_v10_revision"
            ).scalars().all() == [V10]
            if not v10:
                raise ValueError("Unrecognized operational store")
        elif objects == reference(BASELINE):
            legacy = True
        else:
            raise ValueError("Unrecognized database schema")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise ValueError("Repair database references before migrating")
    saved = backup(path) if objects else None
    cfg = config(connection)
    if v10:
        connection.exec_driver_sql("ALTER TABLE operations RENAME TO v10_operations")
    if legacy:
        command.stamp(cfg, BASELINE)
    command.upgrade(cfg, HEAD)
    if v10:
        from setup_system.infrastructure.database.v10 import transfer

        transfer(connection)
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise ValueError("Database migration violated referential integrity")
    return saved


async def _locked_upgrade(engine: AsyncEngine, *, initialize: bool) -> Path | None:
    path = Path(engine.url.database or "")
    await anyio.to_thread.run_sync(prepare_file, engine)
    async with engine.connect() as connection:
        await connection.execution_options(setup_system_write=True)
        async with connection.begin():
            return await connection.run_sync(lambda c: _upgrade(c, path, initialize=initialize))


async def migrate(engine: AsyncEngine) -> Path | None:
    path = Path(engine.url.database or "")
    with schema_lock(path, exclusive=True):
        return await _locked_upgrade(engine, initialize=False)


@asynccontextmanager
async def runtime_schema(engine: AsyncEngine) -> AsyncIterator[None]:
    path = Path(engine.url.database or "")
    if not path.exists() or path.stat().st_size == 0:
        with schema_lock(path, exclusive=True):
            await _locked_upgrade(engine, initialize=True)
    with schema_lock(path, exclusive=False):
        await anyio.to_thread.run_sync(prepare_file, engine)
        async with engine.connect() as connection:
            if await connection.run_sync(current) != HEAD:
                raise ValueError("Run setup-system migrate before opening this database")
            await connection.run_sync(validate_head)
        yield
