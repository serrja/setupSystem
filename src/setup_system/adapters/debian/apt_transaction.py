"""Acquire signed Debian packages, freeze their closure and apply from the private cache."""

from __future__ import annotations

import contextlib
import os
import platform
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from setup_system.adapters.debian.apt_paths import (
    SystemPaths,
    check_offline_namespace,
    validate_package_paths,
)
from setup_system.adapters.debian.apt_selection import freeze_selection, validate_simulation
from setup_system.adapters.debian.happ_artifact import acquire_happ
from setup_system.adapters.debian.happ_service import (
    activate_service,
    check_existing_happ,
    check_happ_unit_policy,
    validate_service_manager,
)
from setup_system.adapters.debian.hyprland import package_arguments
from setup_system.adapters.debian.package_session import (
    APT_TIMEOUT,
    PACKAGE_TIMEOUT,
    AptCommands,
    AptSources,
    system_environment,
)
from setup_system.adapters.debian.service_policy import defer_services
from setup_system.domain.happ import HappPin
from setup_system.domain.package_journal import InstallJournal, InstallStage, InstallStatus
from setup_system.infrastructure.catalog_repository import load_happ_pin, load_policy
from setup_system.infrastructure.package_journal import prepare_journal
from setup_system.infrastructure.processes import run_command
from setup_system.infrastructure.system_files import write_system


@dataclass
class PreparedPackages:
    """Keep one prepared package selection alive until its offline application finishes."""

    _apply: Callable[[], None]
    _activate: Callable[[], None]
    happ_pin: HappPin | None
    _active: bool = True
    _installed: bool = False
    _activated: bool = False

    def install(self) -> None:
        if not self._active:
            raise RuntimeError("Prepared package context is closed")
        if not self._installed:
            self._apply()
            self._installed = True

    def activate_happ(self) -> None:
        """Activate the selected vendor helper after offline installation."""
        if not self._active:
            raise RuntimeError("Prepared package context is closed")
        if self.happ_pin is None:
            raise ValueError("Happ was not selected")
        if not self._installed:
            raise RuntimeError("Offline packages must be installed before Happ activation")
        if not self._activated:
            self._activate()
            self._activated = True


@dataclass
class PackageSession:
    """Own one verified acquisition cache and its concrete privileged operations."""

    commands: AptCommands
    sources: AptSources
    pin: HappPin | None
    paths: SystemPaths
    run: Callable[..., subprocess.CompletedProcess[str]]
    emit: Callable[[str], None]
    journal: InstallJournal = field(default_factory=InstallJournal)
    local_debs: tuple[Path, ...] = ()
    expected_happ_version: str | None = None
    persist_backports: bool = True

    def execute(
        self, argv: list[str], timeout: int = APT_TIMEOUT, *, manager_offline: bool = False
    ) -> subprocess.CompletedProcess[str]:
        environment = system_environment(package_manager=True)
        if manager_offline:
            environment["SYSTEMD_OFFLINE"] = "1"
        result = self.run(
            argv, env=environment, text=True, capture_output=True, check=True, timeout=timeout
        )
        if result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode, argv, result.stdout, result.stderr
            )
        return result

    def checkpoint(
        self,
        stage: InstallStage,
        completed: str | None = None,
        *,
        status: InstallStatus | None = None,
    ) -> None:
        self.journal = self.journal.checkpoint(stage, completed, status=status)
        write_system(self.paths.journal, self.journal.serialize())

    def download(self) -> None:
        stage = self.commands.stage
        for name in ("lists", "lists/partial", "archives", "archives/partial"):
            directory = stage / name
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
        # APT needs both its canonical archive alias and the original local input path offline.
        if self.pin is not None:
            os.link(
                stage / "happ.deb",
                stage / "archives" / self.pin.cache_name(),
                follow_symlinks=False,
            )
        local_versions = {}
        for local in self.local_debs:
            name, version, _arch = local.stem.split("_")
            os.link(local, stage / "archives" / local.name, follow_symlinks=False)
            local_versions[name] = version
        source = stage / "debian.sources"
        source.write_text(self.sources.staging())
        source.chmod(0o644)
        self.emit("Stage: metadata (authorized signed Debian refresh)")
        self.execute(self.commands.update())
        self.checkpoint(InstallStage.RESOLUTION, "metadata")
        simulation = self.execute(self.commands.simulate())
        validate_simulation(simulation.stdout)
        self.commands = self.commands.freeze(
            freeze_selection(
                list(self.commands.selection),
                simulation.stdout,
                self.pin.version if self.pin is not None else "",
                local_versions,
            )
        )
        self.checkpoint(InstallStage.DOWNLOADS, "resolution")
        self.emit("Stage: downloads (packages only; no installation or service changes)")
        self.execute(self.commands.download(), timeout=PACKAGE_TIMEOUT)
        self.checkpoint(InstallStage.DOWNLOADS, "downloads", status=InstallStatus.PREPARED)

    def install(self) -> None:
        self.checkpoint(InstallStage.PACKAGES, status=InstallStatus.RUNNING)
        if self.pin is not None:
            check_existing_happ(
                self.pin.version, expected_happ_version=self.expected_happ_version, run=self.run
            )
            check_happ_unit_policy(self.paths)
        self.emit("Stage: packages (offline network namespace; existing service starts deferred)")
        with defer_services(self.paths.policy):
            self.execute(self.commands.apply(), timeout=PACKAGE_TIMEOUT, manager_offline=True)
        completed = "packages"
        if self.persist_backports:
            self.checkpoint(InstallStage.SOURCE, completed)
            write_system(self.paths.backports, self.sources.managed_backports(), 0o644)
            completed = "source"
        self.checkpoint(
            InstallStage.HAPP_SERVICE if self.pin else InstallStage.PACKAGES,
            completed,
            status=InstallStatus.PACKAGES_COMPLETE,
        )

    def activate_happ(self) -> None:
        self.checkpoint(InstallStage.HAPP_SERVICE, status=InstallStatus.RUNNING)
        check_happ_unit_policy(self.paths)
        activate_service(self.run, self.execute)
        self.checkpoint(
            InstallStage.HAPP_SERVICE, "happ-service", status=InstallStatus.SYSTEM_COMPLETE
        )

    def failed(self, error: BaseException) -> None:
        self.journal = self.journal.failed(error)
        write_system(self.paths.journal, self.journal.serialize())


@contextlib.contextmanager
def prepare_packages(
    root: Path,
    *,
    packages: tuple[str, ...] | None = None,
    graphical_login: bool = True,
    happ: bool = False,
    external_debs: tuple[str, ...] = (),
    expected_happ_version: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    paths: SystemPaths | None = None,
    release: dict[str, str] | None = None,
    architecture: str | None = None,
    emit: Callable[[str], None] = print,
) -> Iterator[PreparedPackages]:
    """Keep one verified staging directory alive from acquisition through offline apply."""
    paths = SystemPaths() if paths is None else paths
    policy = load_policy(root)
    arch = policy.debian_architecture(architecture or platform.machine())
    base = packages is None
    if packages is None:
        if happ or external_debs or expected_happ_version is not None:
            raise ValueError("Select vendor packages in the workspace package plan")
        packages = (
            "python3",
            "ca-certificates",
            *package_arguments(
                root=root,
                os_release=release,
                architecture=architecture,
                graphical_login=graphical_login,
            ),
        )
    sources = AptSources(policy.hyprland.codename, policy.hyprland.backports, paths.keyring)
    validate_package_paths(paths, sources.managed_backports(), persist_backports=base)
    pin = load_happ_pin(root, arch) if happ else None
    if pin is not None:
        check_happ_unit_policy(paths)
        validate_service_manager(root=root, run=run)
        check_existing_happ(pin.version, expected_happ_version=expected_happ_version, run=run)
    check_offline_namespace(run)
    with tempfile.TemporaryDirectory(prefix="setup-system-apt-", dir=paths.runtime) as temporary:
        stage = Path(temporary)
        stage.chmod(0o700)
        args = list(dict.fromkeys(packages))
        if pin is not None:
            args.append(str(acquire_happ(pin, stage, run)))
        local: tuple[Path, ...] = ()
        if external_debs:
            from setup_system.infrastructure.catalog_deb import stage_deb

            local = tuple(stage_deb(root, app_id, arch, stage) for app_id in external_debs)
            args.extend(str(path) for path in local)
        # APT's sandbox user only reads the stage after every local asset is verified.
        stage.chmod(0o755)
        session = PackageSession(
            AptCommands(stage, tuple(args)),
            sources,
            pin,
            paths,
            run,
            emit,
            local_debs=local,
            expected_happ_version=expected_happ_version,
            persist_backports=base,
        )
        prepare_journal(paths.journal)
        session.checkpoint(InstallStage.METADATA)
        prepared = PreparedPackages(session.install, session.activate_happ, pin)
        try:
            session.download()
            yield prepared
        except BaseException as exc:
            session.failed(exc)
            raise
        finally:
            prepared._active = False
