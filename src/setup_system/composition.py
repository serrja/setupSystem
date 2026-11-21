"""Bind installed release resources and native adapters at application startup."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from setup_system.application.installer import InstallationService
from setup_system.application.interaction import ConsoleInteraction, Interaction
from setup_system.application.recovery import recover
from setup_system.application.tools import WorkspaceTools
from setup_system.application.ui_ports import (
    DisplayControls,
    InstallationPort,
    Intentions,
    ViewServices,
)
from setup_system.application.workstation_stages import Executor
from setup_system.domain.catalog import ComponentCatalog
from setup_system.domain.identity import TargetUser
from setup_system.domain.operations import InstallRequest, Operation
from setup_system.domain.settings import SystemSettings, UserSettings
from setup_system.infrastructure import displays, receipt_repository
from setup_system.infrastructure.catalog_deb import last_attempt
from setup_system.infrastructure.catalog_repository import load_cleanup, load_components
from setup_system.infrastructure.config_store import ConfigStore, SettingsPaths, save_intentions
from setup_system.infrastructure.identity import resolve_target
from setup_system.infrastructure.mutation import user_operation
from setup_system.infrastructure.palette import palette
from setup_system.infrastructure.privileged import PrivilegedExecutor
from setup_system.infrastructure.receipt_migrations import restore_manifest
from setup_system.infrastructure.restore_files import LocalRecoveryFiles
from setup_system.infrastructure.theme import preview
from setup_system.tui.installer import InstallationApp


@dataclass(frozen=True)
class LocalSettings:
    paths: SettingsPaths
    catalog: ComponentCatalog

    def draft(self) -> ConfigStore[SystemSettings]:
        return ConfigStore(self.paths.user.parent / "system-intentions.toml", SystemSettings)

    def system(self) -> tuple[SystemSettings, str | None]:
        return ConfigStore(self.paths.system, SystemSettings, uid=0).read()

    def read(self) -> Intentions:
        user, revision = ConfigStore(self.paths.user, UserSettings, catalog=self.catalog).read()
        warning = None
        try:
            system, root_revision = self.system()
        except PermissionError:
            system, root_revision = SystemSettings(), None
            warning = (
                "Base system settings are unreadable. See the system directory recovery "
                "procedure in docs/configuration.md."
            )
        draft, draft_revision = self.draft().read()
        return Intentions(
            user,
            draft if draft_revision is not None else system,
            revision,
            draft_revision,
            root_revision,
            warning,
        )

    def check_draft(self, expected: str | None) -> None:
        if self.draft().read()[1] != expected:
            raise ValueError("System intentions changed on disk. Reopen the application.")

    def save(
        self,
        user: UserSettings,
        system: SystemSettings,
        user_revision: str | None,
        draft_revision: str | None,
    ) -> tuple[str, str]:
        with user_operation():
            return save_intentions(
                user, system, user_revision, draft_revision, catalog=self.catalog
            )


def build_tools(root: Path) -> WorkspaceTools:
    return WorkspaceTools(root, restore, user_operation, receipt_repository.list_operations)


def build_service(
    root: Path,
    *,
    executor: Executor | None = None,
    identity: Callable[[str | None], TargetUser] | None = None,
    interaction: Interaction | None = None,
    authorize: Callable[[], bool] | None = None,
) -> InstallationService:
    paths = SettingsPaths()
    catalog = load_components(root)
    blocker = "System apply requires a published release with native build provenance"
    if executor is None:
        try:
            executor = PrivilegedExecutor.from_marker(paths.state / "release.json")
        except (ValueError, OSError):
            blocker = (
                "Review the delivery record release.json or relaunch a verified release "
                "before applying system changes"
            )
    return InstallationService(
        root,
        paths=paths,
        catalog=catalog,
        executor=executor,
        identity=resolve_target if identity is None else identity,
        authorize=authorize,
        interaction=ConsoleInteraction() if interaction is None else interaction,
        privilege_blocker=blocker,
    )


def build_app(
    root: Path,
    request: InstallRequest | None = None,
    *,
    identity: Callable[[str | None], TargetUser] | None = None,
    service: InstallationPort | None = None,
) -> InstallationApp:
    production = build_service(root, identity=identity) if service is None else None
    if production is not None:
        service = production
        catalog = production.catalog
    else:
        catalog = load_components(root)
    assert service is not None
    views = ViewServices(
        catalog=catalog,
        cleanup=tuple(sorted(load_cleanup(root))),
        palette=palette(root),
        preview=partial(preview, root=root),
        last_attempt=last_attempt,
        settings=LocalSettings(SettingsPaths(), catalog),
        tools=build_tools(root),
        displays=DisplayControls(
            displays.discover_outputs,
            displays.validate_layouts,
            displays.preview_layout,
            displays.PREVIEW_SECONDS,
        ),
    )
    app = InstallationApp(
        request or InstallRequest(operation=Operation.PLAN), service=service, views=views
    )
    service.interaction = app.interaction
    if production is not None:

        def native_authorization() -> bool:
            return subprocess.run(["/usr/bin/sudo", "-v"], check=False).returncode == 0

        production.authorize = lambda: app.with_terminal(native_authorization)
    return app


def restore(backup_id: str, *, apply: bool = False) -> list[tuple[str, str]]:
    folder = receipt_repository.operation_folder(backup_id)
    if receipt_repository.receipt_source(folder) is None:
        return restore_manifest(folder, apply=apply)
    return recover(folder, apply=apply, journal=receipt_repository, files=LocalRecoveryFiles())
