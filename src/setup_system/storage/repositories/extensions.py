"""Register trusted releases and retain history across disable or removal."""

import re

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from setup_system.storage.models import ActiveOperation, Extension, Release
from setup_system.storage.session import begin_write
from setup_system_sdk.schemas.manifest import Manifest


class ExtensionRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def active(self, plugin_id: str) -> str | None:
        async with self.factory() as db:
            return (
                await db.scalars(
                    select(Release.digest).where(Release.plugin_id == plugin_id, Release.active)
                )
            ).one_or_none()

    async def activate(self, manifest: Manifest, digest: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid verified release digest")
        async with self.factory.begin() as db:
            await begin_write(db)
            release = await db.scalar(
                select(Release).where(Release.plugin_id == manifest.id, Release.digest == digest)
            )
            if release is not None and (
                release.source != manifest.source or release.version != manifest.version
            ):
                raise ValueError("The release digest identifies different registered metadata")
            if release is not None and release.active:
                return
            await self._idle(db, manifest.id)
            if await db.get(Extension, manifest.id) is None:
                db.add(Extension(plugin_id=manifest.id))
                await db.flush()
            await db.execute(
                update(Release)
                .where(Release.plugin_id == manifest.id, Release.active)
                .values(active=False)
            )
            if release is None:
                db.add(
                    Release(
                        plugin_id=manifest.id,
                        digest=digest,
                        version=manifest.version,
                        source=manifest.source,
                        active=True,
                        installed=True,
                    )
                )
            else:
                release.installed = release.active = True

    @staticmethod
    async def _idle(db: AsyncSession, plugin_id: str) -> None:
        if await db.get(ActiveOperation, plugin_id) is not None:
            raise ValueError("An unfinished operation requires this extension")

    async def disable(self, plugin_id: str) -> None:
        async with self.factory.begin() as db:
            await begin_write(db)
            await self._idle(db, plugin_id)
            await db.execute(
                update(Release).where(Release.plugin_id == plugin_id).values(active=False)
            )

    async def remove(self, plugin_id: str) -> None:
        async with self.factory.begin() as db:
            await begin_write(db)
            await self._idle(db, plugin_id)
            await db.execute(
                update(Release)
                .where(Release.plugin_id == plugin_id)
                .values(active=False, installed=False)
            )
