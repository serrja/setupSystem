"""Typed, allowlisted system tasks, imported exclusively from verified root source."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from setup_system.adapters.debian.apt_transaction import prepare_packages
from setup_system.bootstrap.source import caller, protected
from setup_system.diagnostics import PartialApplyError, error_text
from setup_system.domain.paths import XdgPaths
from setup_system.domain.settings import SystemSettings
from setup_system.domain.system_commands import (
    BootCommand,
    LoginCommand,
    PackagesCommand,
    PrivilegedCommand,
    RegionalCommand,
    SystemCommand,
)
from setup_system.infrastructure import firefox_policy, system_settings
from setup_system.infrastructure.identity import resolve_target, user_environment
from setup_system.infrastructure.login import DesktopSystem
from setup_system.infrastructure.processes import run_command


def check_xdg(xdg: XdgPaths | None, home: Path) -> dict[str, Path]:
    if xdg is None:
        return {}
    paths = xdg.overrides()
    for path in paths.values():
        if (
            path == home
            or not path.is_relative_to(home)
            or any(p.is_symlink() for p in (path, *path.parents))
        ):
            raise ValueError("XDG paths must stay within caller HOME without symlinks")
    return paths


def execute(root: Path, username: str, command: PrivilegedCommand) -> int:
    if caller().pw_name != username:
        raise ValueError("Caller changed after acquisition")
    protected(root)
    target = resolve_target(username)
    if isinstance(command, RegionalCommand):
        from setup_system.infrastructure import regional

        system_settings.check_revision(command.settings.expected_system_sha256)
        action = command.action
        desired = regional.validate(action, command.settings.value)
        before = regional.actual(action)
        if regional.equivalent(action, before, desired):
            return 0
        if before != command.settings.before:
            raise ValueError("Regional settings changed after plan confirmation")
        needed = regional.prerequisites(action)
        if command.settings.packages != tuple(needed):
            raise ValueError("Regional package prerequisites changed; inspect a new plan")
        if needed:
            _packages(root, needed)
            generated = regional.actual(action)
            if before is None and action == "keyboard" and generated == "us":
                before = generated
        regional.apply(action, desired, before)
    elif isinstance(command, BootCommand):
        from setup_system.infrastructure.graphical_unlock import apply_unlock

        system_settings.check_revision(command.settings.expected_system_sha256)
        apply_unlock(
            command.settings.identity,
            root=root,
            packages=command.settings.packages,
            install=lambda needed: _packages(root, needed),
        )
    elif isinstance(command, (SystemCommand, LoginCommand)):
        system_settings.check_revision(command.settings.expected_system_sha256)
        environment = user_environment(target, check_xdg(command.settings.xdg, target.home))
        if command.action == "system_settings":
            intentions = SystemSettings.model_validate(
                command.settings.model_dump(include=set(SystemSettings.model_fields))
            )
            system_settings.save(intentions, expected=command.settings.expected_system_sha256)
        elif isinstance(command, LoginCommand):
            desktop = DesktopSystem(root, run=run_command)
            desktop.apply(desktop.revalidate(command.settings.confirmed, target, environment))
        else:
            with prepare_packages(
                root, graphical_login=command.settings.graphical_login
            ) as session:
                session.install()
    elif isinstance(command, PackagesCommand):
        from setup_system.infrastructure.catalog_repository import load_components

        catalog = load_components(root).entries
        if not set(command.settings.packages) <= {p for c in catalog for p in c.packages}:
            raise ValueError("Workspace package is not allowlisted")
        if not set(command.settings.external_debs) <= {
            c.id for c in catalog if c.handler == "catalog_deb" and not c.blocker
        }:
            raise ValueError("External Debian package is not allowlisted")
        policy = firefox_policy.inspect() if "firefox-esr" in command.settings.packages else None
        _packages(
            root,
            command.settings.packages,
            happ=command.settings.happ,
            external=command.settings.external_debs,
            expected_happ_version=command.settings.expected_happ_version,
        )
        if policy is not None:
            try:
                firefox_policy.apply(policy)
            except (OSError, ValueError) as exc:
                raise PartialApplyError(error_text(exc)) from exc
    else:
        from setup_system.infrastructure.cleanup import apply_cleanup

        check_xdg(command.settings.xdg, target.home)
        apply_cleanup(command.settings, root=root)
    return 0


def _packages(
    root: Path,
    packages: tuple[str, ...],
    *,
    happ: bool = False,
    external: tuple[str, ...] = (),
    expected_happ_version: str | None = None,
) -> None:
    from setup_system.adapters.debian.packages import installed_version
    from setup_system.infrastructure.catalog_deb import verify_installed
    from setup_system.infrastructure.catalog_repository import load_policy

    with prepare_packages(
        root,
        packages=packages,
        happ=happ,
        external_debs=external,
        expected_happ_version=expected_happ_version,
    ) as session:
        try:
            session.install()
            if any(installed_version(name) is None for name in packages):
                raise RuntimeError("Debian package database readback failed")
            if happ:
                if (
                    session.happ_pin is None
                    or installed_version("happ") != session.happ_pin.version
                ):
                    raise RuntimeError("Happ package version readback failed")
                session.activate_happ()
            if external:
                arch = load_policy(root).debian_architecture(platform.machine())
                for name in external:
                    verify_installed(root, name, arch)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            raise PartialApplyError(error_text(exc)) from exc
