"""Explicit graphical login activation using Debian greetd, cage and GTK Greeter."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import ClassVar

from setup_system.domain.identity import TargetUser
from setup_system.infrastructure.identity import resolve_target, user_environment
from setup_system.infrastructure.native_target import current_boot_identity, current_target
from setup_system.layout import resource_root
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Resource, Result
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.managed_assets import desktop_directories
from setup_system_sdk.native_play import run_native_play

from .login import DesktopSystem
from .models import DesktopPlan, LoginConsent

FOLDER = Path(__file__).parent


class Confirmation(Inputs):
    user: str
    boot: str
    consent: LoginConsent
    roots: dict[str, Path]


def facts(user: TargetUser, roots: dict[str, Path]) -> DesktopPlan:
    if not current_target()[1]:
        raise ValueError("Graphical login activation requires Debian 13.")
    return DesktopSystem(resource_root()).plan(user, user_environment(user, roots))


def snapshot() -> tuple[DesktopPlan, Confirmation]:
    user = resolve_target(None)
    roots = desktop_directories()
    native = facts(user, roots)
    return native, Confirmation(
        user=user.name, roots=roots, boot=current_boot_identity(), consent=native.consent
    )


def activate_plan() -> Plan:
    native, confirmation = snapshot()
    rows = [
        Resource(
            kind="file",
            identity=str(f.path),
            before=f.observed,
            after=hashlib.sha256(f.content).hexdigest(),
            permissions=f.mode,
            ownership="modified"
            if f.observed or str(f.path) in native.consent.deferred
            else "created",
        )
        for f in native.files
    ]
    ready = (
        native.previous_target == "graphical.target"
        and native.consent.manager_enabled
        and all(
            r.before == r.after and item.observed_mode == r.permissions
            for r, item in zip(rows, native.files, strict=True)
        )
    )
    return Plan(
        summary=f"Enable greetd for {native.target.name} and make graphical.target the default.",
        warnings=[
            "The change affects graphical logins on this machine. Reboot remains separate.",
            *("Revalidate: " + p for p in native.consent.deferred),
        ],
        outcome=Outcome.NO_CHANGES if ready else Outcome.READY,
        resources=[
            *rows,
            Resource(
                kind="service",
                identity="greetd.service",
                before=str(native.consent.manager_enabled),
                after="enabled",
                ownership="modified",
            ),
            Resource(
                kind="systemd-target",
                identity="default.target",
                before=native.previous_target,
                after="graphical.target",
                ownership="modified",
            ),
            Resource(
                kind="boot",
                identity=confirmation.boot,
                before=native.consent.intended,
                ownership="preexisting",
            ),
        ],
    )


class Plugin(DebianPackages):
    executable = "/usr/sbin/greetd"
    package_folder = FOLDER
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "activate-login": Inputs,
    }

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == "uninstall":
            from .login_boot import manager_enabled
            from .login_files import DesktopPaths, LoginFiles

            if manager_enabled(LoginFiles(DesktopPaths(), 0)):
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Select and verify another login manager before uninstalling greetd.",
                )
        if action_id == "activate-login":
            return activate_plan()
        return super().prepare(action_id, settings, inputs)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources and plan.resources[0].kind == "file":
            observed = await asyncio.to_thread(activate_plan)
            return Result(
                outcome=Outcome.CHANGED
                if observed.outcome == Outcome.NO_CHANGES
                else Outcome.PARTIAL,
                message="Effective login service, configuration and default target checked.",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id in ("install", "update", "uninstall"):
        apply_packages("greeter", action_id, plan)
        return
    if action_id != "activate-login" or activate_plan() != plan:
        raise ValueError("Login consent changed.")
    _, confirmation = snapshot()
    if (
        confirmation.boot != plan.resources[-1].identity
        or confirmation.consent.intended != plan.resources[-1].before
    ):
        raise ValueError("Recheck the selected login identity.")
    run_native_play("greeter", "activate.yml", confirmation.model_dump(mode="json"), "sudo")


def activate(change: Confirmation) -> None:
    if not current_target()[1] or current_boot_identity() != change.boot:
        raise ValueError("Boot identity changed.")
    account = resolve_target(change.user)
    system = DesktopSystem(resource_root())
    native = system.revalidate(change.consent, account, user_environment(account, change.roots))
    system.apply(native)
