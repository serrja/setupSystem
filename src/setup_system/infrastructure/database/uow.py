"""Independent read scopes and atomic cross-repository write transactions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from setup_system.infrastructure.database.repositories import (
    DependencyRepository,
    OperationRepository,
    ResourceRepository,
)


class Repositories:
    def __init__(self, session: AsyncSession) -> None:
        self.operations = OperationRepository(session)
        self.resources = ResourceRepository(session)
        self.dependencies = DependencyRepository(session)


class UnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    @asynccontextmanager
    async def read(self) -> AsyncIterator[Repositories]:
        async with self.factory() as session:
            yield Repositories(session)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Repositories]:
        async with self.factory.begin() as session:
            await session.connection(execution_options={"setup_system_write": True})
            yield Repositories(session)
