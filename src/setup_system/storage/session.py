"""Open one session per repository transaction and translate database failures."""

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from setup_system.errors import OperationError
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.schemas.errors import Error


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@contextmanager
def _storage_errors(*, write: bool) -> Iterator[None]:
    try:
        yield
    except SQLAlchemyError as error:
        if isinstance(error, OperationalError):
            code = getattr(error.orig, "sqlite_errorcode", None)
            if isinstance(code, int) and code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise OperationError(
                    Error(
                        code=ErrorCode.CONFLICT,
                        retryable=True,
                        message="Database busy. Refresh the operation before trying again.",
                    )
                ) from None
        if isinstance(error, IntegrityError):
            raise OperationError(
                Error(
                    code=ErrorCode.CONFLICT,
                    message="The operation conflicts with a registered release or reservation.",
                )
            ) from None
        if write:
            raise OperationError(
                Error(
                    code=ErrorCode.UNKNOWN_OUTCOME,
                    message="Database write outcome unknown. Inspect current state first.",
                )
            ) from None
        raise OperationError(
            Error(
                code=ErrorCode.STORAGE_UNAVAILABLE,
                message="Could not read the local operation database.",
            )
        ) from None


@asynccontextmanager
async def read_session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    with _storage_errors(write=False):
        async with factory() as session:
            yield session


@asynccontextmanager
async def write_session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Acquire the SQLite writer slot before reading predicates used for a write."""
    with _storage_errors(write=True):
        async with factory.begin() as session:
            await session.connection(execution_options={"setup_system_write": True})
            yield session
