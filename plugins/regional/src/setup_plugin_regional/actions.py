"""Independent Debian locale, console keyboard and timezone actions."""

from __future__ import annotations

import asyncio
from importlib.resources import files as package_files
from pathlib import Path
from zoneinfo import available_timezones

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import Backend, PluginSettings
from setup_system_sdk.providers import CapabilityRequest
from setup_system_sdk.schemas.common import Inputs, Values
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_regional.adapters import xkb
from setup_plugin_regional.adapters.native import (
    current_boot_identity,
    current_target,
    run_native_play,
)
from setup_plugin_regional.enums import PluginAction
from setup_plugin_regional.models import Confirmation, Keyboard, Locale, Timezone

from . import regional
from .models import RegionalKind

FOLDER = Path(str(package_files("setup_plugin_regional").joinpath("resources")))

SETTINGS = ("locale", "keyboard", "timezone")


async def platform_facts(context: PluginContext | None, detail: str = "target") -> Values:
    if context is None:
        raise ValueError("The action requires a selected platform provider")
    return await context.inspect_provider(
        CapabilityRequest(
            capability="system.platform", step_key="platform", payload={"detail": detail}
        )
    )


def choices(provider: str) -> list[str]:
    if provider == "timezone":
        return sorted(available_timezones())
    if provider == "locale":
        if not regional.SUPPORTED.is_file():
            return ["C.UTF-8"]
        return sorted(
            {
                "C.UTF-8",
                *(
                    line.split()[0]
                    for line in regional.SUPPORTED.read_text().splitlines()
                    if line.endswith(".UTF-8 UTF-8")
                ),
            }
        )
    if provider == "keyboard":
        return xkb.keyboard_layouts()
    return []


def change(action_id: str, inputs: Inputs, boot: str) -> Confirmation:
    setting: RegionalKind
    if action_id == PluginAction.TIMEZONE and isinstance(inputs, Timezone):
        setting, values = "timezone", [inputs.value]
    elif action_id == PluginAction.LOCALE and isinstance(inputs, Locale):
        setting, values = "locale", [inputs.value]
    elif action_id == PluginAction.KEYBOARD and isinstance(inputs, Keyboard):
        setting, values = "keyboard", inputs.layouts
    else:
        raise ValueError("Select an independent regional setting.")
    value = regional.validate(setting, ",".join(values))
    if len(set(values)) != len(values) or not set(values).issubset(choices(setting)):
        raise ValueError("Selected value is no longer available.")
    return Confirmation(setting=setting, value=value, before=regional.actual(setting), boot=boot)


def prepare(action_id: str, inputs: Inputs, boot: str) -> Plan:
    request = change(action_id, inputs, boot)
    return Plan(
        summary=f"Set {request.setting} to {request.value}.",
        resources=[
            Resource(
                kind="regional-setting",
                identity=request.setting,
                before=request.before,
                after=request.value,
                ownership=ResourceOwnership.MODIFIED,
            ),
            Resource(kind="boot", identity=request.boot, ownership=ResourceOwnership.PREEXISTING),
        ],
        outcome=Outcome.NO_CHANGES
        if regional.equivalent(request.setting, request.before, request.value)
        else Outcome.READY,
    )


class PluginActions(Backend):
    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        return [
            item
            for item in await run_sync(choices, provider)
            if query.casefold() in item.casefold()
        ][:128]

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        facts = await asyncio.gather(*(run_sync(regional.actual, setting) for setting in SETTINGS))
        return Result(
            outcome=Outcome.READY,
            message=", ".join(
                f"{k}={v or 'unknown'}" for k, v in zip(SETTINGS, facts, strict=True)
            ),
        )

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if action_id in (LifecycleAction.INSTALL, LifecycleAction.UPDATE):
            if context is None:
                raise ValueError("Package planning requires a provider")
            return await self.delegated_plan(action_id, inputs, context)
        facts = await platform_facts(context)
        boot = facts.get("boot")
        if facts.get("supported") is not True or not isinstance(boot, str):
            return Plan(outcome=Outcome.BLOCKED, summary="Regional changes require Debian 13.")
        return await run_sync(prepare, action_id, inputs, boot)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        row = plan.resources[0]
        value = await run_sync(regional.actual, row.identity)
        verified = (
            len(plan.resources) == 2
            and plan.resources[1].kind == "boot"
            and plan.resources[1].identity == (await platform_facts(context)).get("boot")
            and row.after is not None
            and regional.equivalent(row.identity, value, row.after)
        )
        return Result.observed(
            outcome=Outcome.CHANGED if verified else Outcome.PARTIAL,
            message=f"Observed {row.identity}: {value or 'unknown'}.",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if not current_target()[1]:
        raise ValueError("Regional changes require Debian 13.")
    request = change(action_id, inputs, current_boot_identity())
    if request.before != plan.resources[0].before or request.boot != plan.resources[1].identity:
        raise ValueError("The regional setting or boot identity changed.")
    run_native_play("regional", "configure.yml", request.model_dump(mode="json"), "sudo")
