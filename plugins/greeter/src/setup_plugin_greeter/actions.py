"""Explicit graphical login activation using Debian greetd, cage and GTK Greeter."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from importlib.resources import files as package_files
from pathlib import Path

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.account_identity import TargetUser
from setup_system_sdk.files.assets import desktop_directories
from setup_system_sdk.files.backend import NativeBackend
from setup_system_sdk.files.identity import resolve_target, user_environment
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_greeter.adapters.native import (
    current_boot_identity,
    current_target,
    run_native_play,
)
from setup_plugin_greeter.adapters.system_facts import SystemSnapshot
from setup_plugin_greeter.enums import PluginAction
from setup_plugin_greeter.models import Confirmation

from .login import DesktopSystem
from .models import DesktopPlan

FOLDER = Path(str(package_files("setup_plugin_greeter").joinpath("resources")))


async def platform_facts(context: PluginContext | None, detail: str = "target") -> Values:
    if context is None:
        raise ValueError("The action requires a selected platform provider")
    return await context.inspect_provider(
        CapabilityRequest(
            capability="system.platform", step_key="platform", payload={"detail": detail}
        )
    )


def facts(
    user: TargetUser, roots: dict[str, Path], session: SystemSnapshot | None = None
) -> DesktopPlan:
    if session is None and not current_target()[1]:
        raise ValueError("Graphical login activation requires Debian 13.")
    return DesktopSystem(Path(str(files("setup_plugin_greeter") / "resources"))).plan(
        user, user_environment(user, roots), snapshot=session
    )


def snapshot(
    session: SystemSnapshot | None = None, boot: str | None = None
) -> tuple[DesktopPlan, Confirmation]:
    user = resolve_target(None)
    roots = desktop_directories()
    native = facts(user, roots, session)
    return native, Confirmation(
        user=user.name,
        roots=roots,
        boot=boot if boot is not None else current_boot_identity(),
        consent=native.consent,
    )


def activate_plan(session: SystemSnapshot | None = None, boot: str | None = None) -> Plan:
    native, confirmation = snapshot(session, boot)
    rows = [
        Resource(
            kind="file",
            identity=str(f.path),
            before=f.observed,
            after=hashlib.sha256(f.content).hexdigest(),
            permissions=f.mode,
            ownership=ResourceOwnership.MODIFIED
            if f.observed or str(f.path) in native.consent.deferred
            else ResourceOwnership.CREATED,
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
                ownership=ResourceOwnership.MODIFIED,
            ),
            Resource(
                kind="systemd-target",
                identity="default.target",
                before=native.previous_target,
                after="graphical.target",
                ownership=ResourceOwnership.MODIFIED,
            ),
            Resource(
                kind="boot",
                identity=confirmation.boot,
                before=native.consent.intended,
                ownership=ResourceOwnership.PREEXISTING,
            ),
        ],
    )


class PluginActions(NativeBackend):
    executable = "/usr/sbin/greetd"

    package_folder = FOLDER

    async def login_plan(self, context: PluginContext | None) -> Plan:
        facts = await platform_facts(context, "session")
        boot = facts.get("boot")
        if facts.get("supported") is not True or not isinstance(boot, str):
            return Plan(
                summary="Graphical login requires a supported Debian target.",
                outcome=Outcome.BLOCKED,
            )
        return await run_sync(activate_plan, SystemSnapshot.model_validate(facts["session"]), boot)

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if action_id == PluginAction.ACTIVATE_LOGIN:
            return await self.login_plan(context)
        return await super().plan(action_id, settings, inputs, context)

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == LifecycleAction.UNINSTALL:
            from .login_boot import manager_enabled
            from .login_files import DesktopPaths, LoginFiles

            if manager_enabled(LoginFiles(DesktopPaths(), 0)):
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Select and verify another login manager before uninstalling greetd.",
                )
        if action_id == PluginAction.ACTIVATE_LOGIN:
            return activate_plan()
        return super().prepare(action_id, settings, inputs)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if plan.resources and plan.resources[0].kind == "file":
            observed = await self.login_plan(context)
            return Result.observed(
                outcome=Outcome.CHANGED
                if observed.outcome == Outcome.NO_CHANGES
                and [(r.kind, r.identity, r.after, r.permissions) for r in observed.resources]
                == [(r.kind, r.identity, r.after, r.permissions) for r in plan.resources]
                else Outcome.PARTIAL,
                message="Effective login service, configuration and default target checked.",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id != PluginAction.ACTIVATE_LOGIN or activate_plan() != plan:
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
    system = DesktopSystem(Path(str(files("setup_plugin_greeter") / "resources")))
    native = system.revalidate(change.consent, account, user_environment(account, change.roots))
    system.apply(native)
