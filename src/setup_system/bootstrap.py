"""Own host services and their asynchronous lifetime."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import anyio
import httpx
from platformdirs import user_data_path, user_state_path
from setup_system_sdk.downloads import GITHUB_RELEASE_HOSTS, DownloadClient
from setup_system_sdk.processes import ProcessRunner

from setup_system.application.ledger import LedgerService
from setup_system.application.operations import OperationService
from setup_system.application.plugins import PluginService
from setup_system.application.providers import ProviderBroker
from setup_system.application.recovery import RecoveryService
from setup_system.configuration.profiles import SettingsStore
from setup_system.configuration.settings import Settings
from setup_system.errors import OperationError, normalize_failure
from setup_system.infrastructure.database.engine import create_engine, session_factory
from setup_system.infrastructure.database.migrations import runtime_schema
from setup_system.infrastructure.database.uow import UnitOfWork
from setup_system.infrastructure.plugins.discovery import TrustPolicy
from setup_system.infrastructure.plugins.registry import PluginRegistry
from setup_system.infrastructure.plugins.workers import WorkerClient
from setup_system.infrastructure.trust import NativeLauncher, TrustStore
from setup_system.journal import Journal


@dataclass(frozen=True)
class Runtime:
    plugins: PluginService
    operations: OperationService
    recovery: RecoveryService
    journal: Journal


@asynccontextmanager
async def runtime(settings: Settings | None = None) -> AsyncIterator[Runtime]:
    options = settings or Settings()
    engine = create_engine(options)
    uow = UnitOfWork(session_factory(engine))
    locks = Path(engine.url.database or "").with_suffix(".locks")
    registry = PluginRegistry(
        user_data_path("setup-system") / "v10" / "extensions", options.providers
    )
    store = SettingsStore(profile=options.profile)
    journal = Journal(user_state_path("setup-system") / "diagnostics")

    def client() -> WorkerClient:
        return WorkerClient(
            ProcessRunner(),
            options.read_only_timeout_seconds,
            journal,
            terminal_timeout=options.terminal_timeout_seconds,
            idle_timeout=options.idle_timeout_seconds,
            grace=options.termination_grace_seconds,
        )

    workers = client()
    # A provider worker may run while its consumer holds a worker permit.
    broker = ProviderBroker(registry, client())
    try:
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(runtime_schema(engine))
            except Exception as failure:
                detail = normalize_failure(failure, stage="database")
                try:
                    await journal.emit_failure(detail, failure, stage="database")
                except Exception:
                    pass
                raise OperationError(detail) from None
            http = await stack.enter_async_context(httpx.AsyncClient(trust_env=False))
            tasks = await stack.enter_async_context(anyio.create_task_group())
            plugins = PluginService(
                registry,
                broker,
                workers,
                store,
                uow,
                locks,
                DownloadClient(http),
                TrustPolicy(hosts=GITHUB_RELEASE_HOSTS),
                options.catalogs,
                journal,
            )
            for item in await plugins.catalog():
                if item.manifest and not item.disabled and not item.reason:
                    try:
                        active = await plugins.active_release(item.manifest.id)
                    except ValueError:
                        continue
                    plugins.startup_ui[item.manifest.id] = active.digest
            operations = OperationService(
                registry,
                broker,
                workers,
                plugins.profile,
                LedgerService(uow),
                locks,
                NativeLauncher(
                    TrustStore(Path("/etc/setup-system/executors")),
                    ProcessRunner(),
                    prompt_timeout=options.prompt_timeout_seconds,
                ),
                tasks,
            )
            try:
                yield Runtime(plugins, operations, RecoveryService(uow, operations), journal)
            finally:
                for token in operations.pending.values():
                    token.set()
    finally:
        with anyio.CancelScope(shield=True):
            await engine.dispose()
