"""Catalog, settings and generation lifecycle for user-selected plugins."""

from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from uuid import uuid4

import anyio
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.downloads import DownloadClient
from setup_system_sdk.enums.errors import ErrorCode
from setup_system_sdk.enums.operation import WorkerMethod
from setup_system_sdk.files.generations import bounded_file
from setup_system_sdk.manifests import Manifest
from setup_system_sdk.schemas.common import Values
from setup_system_sdk.schemas.errors import Error
from setup_system_sdk.schemas.wire import Reply, Request

from setup_system.application.providers import ProviderBroker
from setup_system.application.recovery import retained_for_recovery
from setup_system.configuration.profiles import SavedValues, SettingsStore
from setup_system.errors import OperationError, normalize_failure
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
from setup_system.journal import Journal


def accepts_previous_requests(previous: Values, candidate: Values) -> bool:
    """Accept unchanged validation rules and optional additions to a closed request model."""

    def rules(schema: Values) -> Values:
        return {k: v for k, v in schema.items() if k not in {"title", "description", "examples"}}

    old, new = rules(previous), rules(candidate)
    old_fields, new_fields = old.pop("properties", {}), new.pop("properties", {})
    required, now_required = old.pop("required", []), new.pop("required", [])
    if (
        old != new
        or not isinstance(old_fields, dict)
        or not isinstance(new_fields, dict)
        or not isinstance(required, list)
        or not isinstance(now_required, list)
        or any(k not in required for k in now_required)
    ):
        return False
    if old.get("additionalProperties") is not False and new_fields.keys() - old_fields.keys():
        return False
    for name, field in old_fields.items():
        updated = new_fields.get(name)
        if (
            not isinstance(field, dict)
            or not isinstance(updated, dict)
            or rules(field) != rules(updated)
        ):
            return False
    return True


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
        journal: Journal | None = None,
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
        self.journal = journal
        self.startup_ui: dict[str, str] = {}

    async def active_release(self, plugin: str) -> Activation:
        return await anyio.to_thread.run_sync(self.registry.resolve, plugin)

    async def action_requirements(self, manifest: Manifest) -> dict[str, str]:
        """Report only capability requirements unresolved by the active provider registry."""
        if not any(action.requires for action in manifest.actions):
            return {}
        candidates = await self.catalog()
        blocked = {}
        for action in manifest.actions:
            missing = []
            for requirement in action.requires:
                try:
                    await run_sync(self.registry.provider, requirement)
                except ValueError:
                    compatible = sorted(
                        row.manifest.id
                        for row in candidates
                        if row.manifest
                        and any(
                            capability.id == requirement.id
                            and Version(capability.version) in SpecifierSet(requirement.version)
                            for capability in row.manifest.provides
                        )
                    )
                    suggestion = (
                        " Select one compatible provider: " + ", ".join(compatible) + "."
                        if len(compatible) > 1
                        else " Activate the compatible provider in the catalog."
                        if compatible
                        else " Add a compatible provider release."
                    )
                    missing.append("Requires " + requirement.id + "." + suggestion)
            if missing:
                blocked[action.id] = " ".join(missing)
        return blocked

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

    async def _require_idle(self, plugin: str, *, removing: bool = False) -> set[str]:
        async with self.uow.read() as repos:
            if await repos.operations.history(plugin, unfinished=True) or (
                await repos.dependencies.pending_dependents(plugin)
            ):
                raise ValueError("Resolve unfinished operations using this plugin first")
            dependents = await repos.dependencies.dependents(plugin)
            if removing and dependents:
                raise ValueError("Installed consumers still require this plugin")
            return dependents

    def _check_contracts(self, candidate: Manifest, dependents: set[str]) -> None:
        if not dependents:
            return
        previous = self.registry.resolve(candidate.id).manifest
        contracts = {c.id: c for c in previous.provides}
        offered = {c.id: c for c in candidate.provides}
        installed_consumers = {
            item.manifest.id: item.manifest
            for item in installed(self.registry.root)
            if item.manifest and not item.reason
        }
        for consumer in dependents:
            manifest = installed_consumers.get(consumer)
            if not manifest:
                raise ValueError(f"Restore the manifest of dependent {consumer} before updating")
            for action in manifest.actions:
                for req in action.requires:
                    if req.id not in contracts:
                        continue
                    contract = offered.get(req.id)
                    if (
                        contract is None
                        or Version(contract.version) not in SpecifierSet(req.version)
                        or not accepts_previous_requests(
                            contracts[req.id].request_schema, contract.request_schema
                        )
                    ):
                        raise ValueError(f"{consumer} requires a compatible {req.id} provider")

    async def activate(
        self,
        release: Release,
        approved_digest: str,
        wheelhouse: Path | None = None,
        progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> Activation:
        if release.digest != approved_digest:
            raise OperationError(
                Error(
                    code=ErrorCode.INTEGRITY_MISMATCH,
                    message="The selected release differs from its approved SHA-256 digest.",
                    hint="Review the release descriptor and enter its verified digest again.",
                )
            )
        plugin = release.manifest.id

        async def validate(candidate: Activation) -> None:
            saved = await self._profile(candidate, persist=False)
            await self.workers.call(
                candidate,
                Request(method=WorkerMethod.VALIDATE, plugin_id=plugin, saved=saved.values),
            )

        try:
            async with acquire(self.locks, ["plugin/" + plugin], wait=False):
                if progress:
                    await progress("Checking plugin activation prerequisites.")
                try:
                    dependents = await self._require_idle(plugin)
                    await run_sync(self._check_contracts, release.manifest, dependents)
                except ValueError as failure:
                    raise OperationError(
                        Error(
                            code=ErrorCode.CONFLICT,
                            message="Operations or dependent contracts block this plugin update.",
                            hint="Finish pending operations and review the provider compatibility.",
                        )
                    ) from failure
                state = await install_release(
                    release,
                    policy=self.policy.model_copy(
                        update={"digests": self.policy.digests | {approved_digest}}
                    ),
                    env_root=self.registry.root,
                    wheelhouse=wheelhouse,
                    validate_settings=validate,
                    client=self.client,
                    progress=progress,
                )
                try:
                    await self._profile(state, persist=True)
                except Exception as failure:
                    try:
                        published = await run_sync(self.registry.resolve, plugin)
                    except (OSError, ValueError):
                        published = None
                    if published is None or published.executor != state.executor:
                        raise OperationError(
                            Error(
                                code=ErrorCode.UNKNOWN_OUTCOME,
                                message="Plugin publication needs a release readback.",
                                hint="Inspect the active release before retrying activation.",
                            )
                        ) from failure
                    raise OperationError(
                        Error(
                            code=ErrorCode.CONFIGURATION_INVALID,
                            message="The release is active, but its saved settings are not ready.",
                            hint="Review the plugin settings, then retry the same release.",
                        )
                    ) from failure
                if progress:
                    await progress(f"{state.manifest.name} {state.manifest.version} is active.")
                return state
        except Exception as failure:
            detail = normalize_failure(failure, stage="activation", component=plugin)
            if self.journal is not None:
                try:
                    await self.journal.emit_failure(
                        detail, failure, stage="activation", plugin_id=plugin
                    )
                except Exception:
                    pass
            raise OperationError(detail) from None

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
            await self._require_idle(plugin, removing=not enabled)
            await anyio.to_thread.run_sync(
                partial(toggle, self.registry.root, plugin, enable=enabled)
            )

    async def removal_files(self, plugin: str) -> tuple[str, ...]:
        return await anyio.to_thread.run_sync(removable, self.registry.root, plugin)

    async def remove(self, plugin: str, files: tuple[str, ...]) -> None:
        async with acquire(self.locks, ["plugin/" + plugin], wait=False):
            await self._require_idle(plugin, removing=True)
            if await retained_for_recovery(self.uow, plugin, self.registry):
                raise ValueError("An operation still requires this plugin for recovery")
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
