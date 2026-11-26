"""Catalog, settings and generation lifecycle for user-selected plugins."""

from functools import partial
from pathlib import Path
from uuid import uuid4

import anyio
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.downloads import DownloadClient
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.files.generations import bounded_file
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.wire import Reply, Request

from setup_system.application.providers import ProviderBroker
from setup_system.configuration.profiles import SavedValues, SettingsStore
from setup_system.infrastructure.database.uow import UnitOfWork
from setup_system.infrastructure.locks import acquire
from setup_system.infrastructure.plugins.discovery import (
    RELEASE_BYTES,
    Release,
    TrustPolicy,
    entry_point_catalog,
    preview_link,
    release_catalog,
)
from setup_system.infrastructure.plugins.discovery import (
    CatalogEntry as CatalogEntry,
)
from setup_system.infrastructure.plugins.environments import Activation, install_release, installed
from setup_system.infrastructure.plugins.lifecycle import removable, retire, toggle
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient


class PluginService:
    def __init__(
        self,
        registry: PluginRegistry,
        broker: ProviderBroker,
        workers: WorkerClient,
        store: SettingsStore,
        uow: UnitOfWork,
        locks: Path,
        client: DownloadClient,
        policy: TrustPolicy,
        catalogs: tuple[Path, ...] = (),
    ) -> None:
        self.registry = registry
        self.broker = broker
        self.workers = workers
        self.store = store
        self.uow = uow
        self.locks = locks
        self.client = client
        self.policy = policy
        self.catalogs = catalogs
        self.startup_ui: dict[str, str] = {}

    async def active_release(self, plugin: str) -> Activation:
        return await anyio.to_thread.run_sync(self.registry.resolve, plugin)

    async def catalog(self) -> tuple[CatalogEntry, ...]:
        releases = await run_sync(release_catalog, self.catalogs)
        release_ids = {e.manifest.id for e in releases if e.manifest}
        packaged = (
            *releases,
            *(
                e
                for e in await run_sync(entry_point_catalog)
                if not e.manifest or e.manifest.id not in release_ids
            ),
        )
        selected = await anyio.to_thread.run_sync(installed, self.registry.root)
        selected_ids = {e.manifest.id for e in selected if e.manifest}
        return (
            *selected,
            *(e for e in packaged if not e.manifest or e.manifest.id not in selected_ids),
        )

    async def preview_release(self, url: str) -> Release:
        if Path(url).is_absolute():
            release = Release(await run_sync(bounded_file, Path(url), RELEASE_BYTES))
            await run_sync(release.selected)
            return release
        return await preview_link(url, self.policy, self.client)

    async def _require_idle(self, plugin: str) -> None:
        async with self.uow.read() as repos:
            if await repos.operations.history(
                plugin, unfinished=True
            ) or await repos.dependencies.dependents(plugin):
                raise ValueError("Resolve unfinished operations and active dependents first")

    async def activate(
        self, release: Release, approved_digest: str, wheelhouse: Path | None = None
    ) -> Activation:
        if release.digest != approved_digest:
            raise ValueError("Release digest differs from the independently approved digest")
        plugin = release.manifest.id

        async def validate(candidate: Activation) -> None:
            saved = await self._profile(candidate, persist=False)
            await self.workers.call(
                candidate,
                Request(method=WorkerMethod.VALIDATE, plugin_id=plugin, saved=saved.values),
            )

        async with acquire(self.locks, ["plugin/" + plugin], wait=False):
            await self._require_idle(plugin)
            state = await install_release(
                release,
                policy=self.policy.model_copy(
                    update={"digests": self.policy.digests | {approved_digest}}
                ),
                env_root=self.registry.root,
                wheelhouse=wheelhouse,
                validate_settings=validate,
                client=self.client,
            )
            await self._profile(state, persist=True)
            return state

    async def _profile(self, release: Activation, *, persist: bool) -> SavedValues:
        plugin = release.manifest.id
        saved = await self.store.read(plugin)
        version = release.manifest.settings_version
        if saved.version == version or saved.revision.endswith(":absent"):
            return saved
        reply = await self.workers.call(
            release,
            Request(
                method=WorkerMethod.MIGRATE,
                plugin_id=plugin,
                saved=saved.values,
                settings_version=saved.version,
            ),
        )
        if persist:
            return await self.store.write(plugin, reply.settings, version, saved.revision)
        return SavedValues(reply.settings, saved.revision, version)

    async def profile(self, plugin: str) -> SavedValues:
        release = await self.active_release(plugin)
        with self.registry.leases({release.executor}):
            return await self._profile(release, persist=False)

    async def enable(self, plugin: str, enabled: bool) -> None:
        async with acquire(self.locks, ["plugin/" + plugin], wait=False):
            await self._require_idle(plugin)
            await anyio.to_thread.run_sync(
                partial(toggle, self.registry.root, plugin, enable=enabled)
            )

    async def removal_files(self, plugin: str) -> tuple[str, ...]:
        return await anyio.to_thread.run_sync(removable, self.registry.root, plugin)

    async def remove(self, plugin: str, files: tuple[str, ...]) -> None:
        async with acquire(self.locks, ["plugin/" + plugin], wait=False):
            await self._require_idle(plugin)
            await anyio.to_thread.run_sync(retire, self.registry.root, plugin, files)

    async def query(self, request: Request) -> Reply:
        release = await anyio.to_thread.run_sync(self.registry.resolve, request.plugin_id)
        saved = await self.profile(request.plugin_id)
        key = request.operation_id or uuid4()
        requirements = {
            r
            for action in release.manifest.actions
            if request.action_id is None or request.action_id == action.id
            for r in action.requires
        }
        providers = {}
        for requirement in requirements:
            try:
                providers[requirement.id] = await run_sync(self.registry.provider, requirement)
            except ValueError:
                continue
        async with self.uow.read() as repositories:
            holdings = tuple(await repositories.resources.list_holdings(request.plugin_id))
        return await self.workers.call(
            release,
            request.model_copy(
                update={"saved": saved.values, "operation_id": key, "holdings": holdings}
            ),
            route=self.broker.route(key, providers, [], holdings),
        )

    async def save(self, plugin: str, values: Values, revision: str) -> SavedValues:
        release = await anyio.to_thread.run_sync(self.registry.resolve, plugin)
        await self.workers.call(
            release, Request(method=WorkerMethod.VALIDATE, plugin_id=plugin, saved=values)
        )
        return await self.store.write(plugin, values, release.manifest.settings_version, revision)
