"""One async SQLite engine per process with bounded locking and transactional DDL."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Protocol

from setup_system_sdk.files.user_storage import private_directory, safe_file
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from setup_system.configuration.settings import Settings


class _Cursor(Protocol):
    def execute(self, statement: str, /) -> None: ...
    def close(self) -> None: ...


class _DriverConnection(Protocol):
    isolation_level: str | None

    def cursor(self) -> _Cursor: ...


def prepare_file(engine: AsyncEngine) -> None:
    """Create a private database file before SQLite opens its path or journal."""
    path = Path(engine.url.database or "")
    uid = os.getuid()
    with private_directory(path.parent) as parent:
        for suffix in ("", "-journal", "-wal", "-shm"):
            safe_file(path.with_name(path.name + suffix), uid)
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (
                info.st_uid != uid
                or info.st_nlink != 1
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
            ):
                raise ValueError("Use a private, single-link database file owned by this account")
        finally:
            os.close(fd)


def create_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"timeout": settings.database_busy_timeout_ms / 1000},
        hide_parameters=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def initialize(dbapi: _DriverConnection, record: ConnectionPoolEntry) -> None:
        dbapi.isolation_level = None
        cursor = dbapi.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection: Connection) -> None:
        # Acquire the writer slot before checking an ownership predicate or schema.
        immediate = connection.get_execution_options().get("setup_system_write", False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
