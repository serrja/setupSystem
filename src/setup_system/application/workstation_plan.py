"""Construct the finite system and user stages described by a confirmed selection."""

from __future__ import annotations

import platform
from pathlib import Path

from setup_system.domain.catalog import ComponentCatalog
from setup_system.domain.operations import (
    Action,
    ApplicationAction,
    BlockedAction,
    CleanupAction,
    DesktopAction,
    LanguageAction,
    PackageAction,
    RegionAction,
)
from setup_system.domain.settings import SystemSettings, UserSettings
from setup_system.domain.system_commands import RegionalKind
from setup_system.infrastructure import regional
from setup_system.infrastructure.catalog_repository import load_policy


def platform_blocker(root: Path) -> tuple[str, ...]:
    release = platform.freedesktop_os_release()
    policy = load_policy(root)
    profile = policy.hyprland
    if (
        release.get("ID") == profile.distribution
        and release.get("VERSION_ID") == profile.version
        and release.get("VERSION_CODENAME") == profile.codename
        and policy.normalize_architecture(platform.machine()) in profile.architectures
    ):
        return ()
    return (
        f"Workstation actions require Debian {profile.version} ({profile.codename}), "
        + "/".join(profile.apt_architectures),
    )


def regional_actions(
    settings: SystemSettings, blocked: tuple[str, ...], *, supported: bool
) -> tuple[list[Action], bool]:
    stages: list[Action] = []
    matched = False
    selections: tuple[tuple[RegionalKind, str | None], ...] = (
        ("timezone", settings.timezone),
        ("locale", settings.locale),
        ("keyboard", settings.keyboard),
    )
    for key, desired in selections:
        if desired is None:
            continue
        before = regional.actual(key)
        try:
            regional.validate(key, desired)
            reasons = blocked
        except ValueError as exc:
            reasons = (*blocked, str(exc))
        if regional.equivalent(key, before, desired) and supported and reasons == blocked:
            matched = True
            continue
        packages = regional.prerequisites(key)
        dependencies = " (requires " + ", ".join(packages) + ")" if packages else ""
        stages.append(
            RegionAction(
                id=key,
                description=f"Set system {key} to {desired}" + dependencies,
                blockers=reasons,
                before=before,
                value=desired,
                packages=packages,
            )
        )
    return stages, matched


def desktop_changes(
    root: Path,
    system: SystemSettings,
    settings: UserSettings,
    blockers: tuple[str, ...],
    *,
    full: bool,
) -> Action | None:
    from setup_system.application.workstation import desktop_drift, theme_files
    from setup_system.infrastructure.displays import validate_layouts
    from setup_system.infrastructure.managed_files import changed_files
    from setup_system.infrastructure.theme import preview

    description = (
        "User session, portals, fonts and theme" if full else preview(settings.theme, root=root)
    )
    if not blockers:
        try:
            if settings.monitors:
                validate_layouts(settings.monitors)
            if full:
                paths = desktop_drift(
                    root, theme=settings.theme, keyboard=system.keyboard, monitors=settings.monitors
                )
            else:
                paths = changed_files(
                    theme_files(
                        root,
                        theme=settings.theme,
                        keyboard=system.keyboard,
                        monitors=settings.monitors,
                    )
                )
            if not paths:
                return None
            description += "\n" + "\n".join(str(path) for path in paths)
        except (OSError, ValueError, RuntimeError) as exc:
            blockers = (str(exc),)
    return DesktopAction(
        id="desktop_files" if full else "theme",
        description=description,
        blockers=blockers,
        requires=("base",) if full else (),
    )


def workspace_actions(
    root: Path,
    catalog: ComponentCatalog,
    system: SystemSettings,
    settings: UserSettings,
    system_blocked: tuple[str, ...],
    user_blocked: tuple[str, ...],
) -> tuple[list[Action], bool]:

    stages: list[Action] = []
    matched = False
    if settings.desktop_theme:
        theme = desktop_changes(root, system, settings, user_blocked, full=False)
        if theme:
            stages.append(theme)
        else:
            matched = True
    components = catalog.selected(settings)
    packages = tuple(dict.fromkeys(p for c in components for p in c.packages))
    external = tuple(c.id for c in components if c.handler == "catalog_deb")
    if packages or ("happ" in settings.apps) or external:
        version = None
        description = "Install verified packages: " + " ".join((*packages, *external))
        if "happ" in settings.apps and not user_blocked:
            from setup_system.adapters.debian import happ_service
            from setup_system.adapters.debian.packages import installed_version
            from setup_system.infrastructure.catalog_repository import load_happ_pin

            policy = load_policy(root)
            pin = load_happ_pin(
                root,
                policy.debian_architecture(platform.machine()),
            )
            version = installed_version("happ")
            description += (
                f" Happ {version or 'absent'} → {pin.version}; activate its vendor helper"
            )
            try:
                happ_service.check_existing_happ(pin.version, expected_happ_version=version)
            except ValueError as exc:
                system_blocked = (*system_blocked, str(exc))
        stages.append(
            PackageAction(
                description=description,
                blockers=system_blocked,
                packages=packages,
                external=external,
                before=version,
            )
        )
    for component in sorted(components, key=lambda item: item.handler == "neovim"):
        blockers = (*user_blocked, *((component.blocker,) if component.blocker else ()))
        if component.kind == "language" and not blockers:
            from setup_system.infrastructure.language_tools import language_blocker

            reason = language_blocker(root, component.id)
            if reason:
                blockers += (reason,)
        if component.handler in {"apt", "happ", "catalog_deb"} and not blockers:
            continue
        requires: tuple[str, ...] = ("workspace_packages",) if component.packages else ()
        if component.handler == "neovim":
            requires += tuple(item.id for item in components if item.kind == "language")
        if component.handler in {"neovim", "firefox", "codex"}:
            stages.append(
                ApplicationAction(
                    id=component.handler,
                    description=component.title,
                    blockers=blockers,
                    requires=requires,
                )
            )
        elif component.handler in {"language", "python_tools"}:
            stages.append(
                LanguageAction.model_validate(
                    {
                        "id": component.id,
                        "description": component.title,
                        "blockers": blockers,
                        "requires": requires,
                    }
                )
            )
        else:
            stages.append(
                BlockedAction(
                    id=component.id,
                    description=component.title,
                    blockers=blockers,
                    requires=requires,
                )
            )
    if settings.cleanup:
        from setup_system.infrastructure.cleanup import inspect_cleanup

        if system_blocked:
            stages.append(
                BlockedAction(
                    id="cleanup",
                    description="Preview optional package cleanup",
                    blockers=system_blocked,
                )
            )
        else:
            report = inspect_cleanup(settings.cleanup, settings.cleanup_mode, root=root)
            if report.requested:
                stages.append(
                    CleanupAction(
                        description=report.simulation, packages=tuple(sorted(report.removed))
                    )
                )
            else:
                matched = True
    return stages, matched
