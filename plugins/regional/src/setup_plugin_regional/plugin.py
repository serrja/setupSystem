"""Independent Debian locale, console keyboard and timezone actions."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar
from zoneinfo import available_timezones

from pydantic import Field

from setup_plugin_regional.enums import PluginAction
from setup_system.execution.native_target import current_boot_identity, current_target
from setup_system.execution.recipes import xkb
from setup_system.execution.recipes.apt import apply_packages, package_plan, verify_packages
from setup_system.execution.recipes.native_play import run_native_play
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import Backend, PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from . import regional
from .change import Confirmation
from .models import RegionalKind

FOLDER = Path(__file__).parent
SETTINGS = ("locale", "keyboard", "timezone")


class Timezone(Inputs):
    value: str = Field(
        description="Use the selected native value for this operation.",
        title="Timezone",
        max_length=128,
        pattern=r"^[A-Za-z0-9/_.+-]+$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "timezone"}},
    )


class Locale(Inputs):
    value: str = Field(
        description="Use the selected native value for this operation.",
        title="UTF-8 locale",
        max_length=32,
        pattern=r"^(C|[a-z]{2,3}_[A-Z]{2})\.UTF-8$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "locale"}},
    )


class Keyboard(Inputs):
    layouts: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,15}$")]] = Field(
        title="Keyboard layouts in order",
        description="The first selected layout is the default.",
        min_length=1,
        max_length=4,
        json_schema_extra={"ui": {"widget": "selection_list", "choice_provider": "keyboard"}},
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


def change(action_id: str, inputs: Inputs) -> Confirmation:
    setting: RegionalKind
    if action_id == PluginAction.TIMEZONE and isinstance(inputs, Timezone):
        setting, values = "timezone", [inputs.value]
    elif action_id == PluginAction.LOCALE and isinstance(inputs, Locale):
        setting, values = "locale", [inputs.value]
    elif action_id == PluginAction.KEYBOARD and isinstance(inputs, Keyboard):
        setting, values = "keyboard", inputs.layouts
    else:
        raise ValueError("Select an independent regional setting.")
    if not current_target()[1]:
        raise ValueError("Regional changes require Debian 13.")
    value = regional.validate(setting, ",".join(values))
    if len(set(values)) != len(values) or not set(values).issubset(choices(setting)):
        raise ValueError("Selected value is no longer available.")
    return Confirmation(
        setting=setting, value=value, before=regional.actual(setting), boot=current_boot_identity()
    )


def prepare(action_id: str, inputs: Inputs) -> Plan:
    request = change(action_id, inputs)
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


class Plugin(Backend):
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        LifecycleAction.INSTALL: Inputs,
        LifecycleAction.UPDATE: Inputs,
        PluginAction.LOCALE: Locale,
        PluginAction.KEYBOARD: Keyboard,
        PluginAction.TIMEZONE: Timezone,
    }

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        return [
            item
            for item in await asyncio.to_thread(choices, provider)
            if query.casefold() in item.casefold()
        ][:128]

    async def inspect(self, settings: PluginSettings) -> Result:
        facts = await asyncio.gather(
            *(asyncio.to_thread(regional.actual, setting) for setting in SETTINGS)
        )
        return Result(
            outcome=Outcome.READY,
            message=", ".join(
                f"{k}={v or 'unknown'}" for k, v in zip(SETTINGS, facts, strict=True)
            ),
        )

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id in (LifecycleAction.INSTALL, LifecycleAction.UPDATE):
            return await asyncio.to_thread(package_plan, FOLDER)
        return await asyncio.to_thread(prepare, action_id, inputs)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources and all(row.kind == "package" for row in plan.resources):
            return await asyncio.to_thread(verify_packages, plan)
        row = plan.resources[0]
        value = await asyncio.to_thread(regional.actual, row.identity)
        verified = (
            len(plan.resources) == 2
            and plan.resources[1].kind == "boot"
            and plan.resources[1].identity == await asyncio.to_thread(current_boot_identity)
            and row.after is not None
            and regional.equivalent(row.identity, value, row.after)
        )
        return Result.observed(
            outcome=Outcome.CHANGED if verified else Outcome.PARTIAL,
            message=f"Observed {row.identity}: {value or 'unknown'}.",
            resources=plan.resources,
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id in (LifecycleAction.INSTALL, LifecycleAction.UPDATE):
        apply_packages("regional", action_id, plan)
        return
    request = change(action_id, inputs)
    if request.before != plan.resources[0].before or request.boot != plan.resources[1].identity:
        raise ValueError("The regional setting or boot identity changed.")
    run_native_play("regional", "configure.yml", request.model_dump(mode="json"), "sudo")
