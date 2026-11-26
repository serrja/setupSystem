"""Create independent, short-lived sessions for each repository transaction."""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def begin_write(session: AsyncSession) -> None:
    """Acquire the SQLite writer slot before reading predicates used for a write."""
    await session.connection(execution_options={"setup_system_write": True})
