"""Manage the Debian snapd runtime and selected applications from the approved Store catalog."""

import asyncio
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import ClassVar

from setup_plugin_snap import actions, daemon
from setup_plugin_snap.catalog import Selection, catalog
from setup_plugin_snap.enums import SnapAction
from setup_plugin_snap.removal_guard import require_empty
from setup_system.execution.recipes.apt import DebianPackages, apply_packages, installed
from setup_system.execution.recipes.native_play import run_native_play
from setup_system_sdk.enums.lifecycle import LifecycleAction, ServiceAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import Command, PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result


class Plugin(DebianPackages):
    executable = "/usr/bin/snap"
    package_folder = Path(__file__).parent
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        ServiceAction.START: Inputs,
        SnapAction.LIST_INSTALLED: Inputs,
    }

    def commands(self) -> dict[str, Command]:
        registry = super().commands()
        for action in (
            SnapAction.INSTALL_APPS,
            SnapAction.UPDATE_APPS,
            SnapAction.REMOVE_APPS,
            SnapAction.CHECK_APPS,
        ):
            registry[action.value] = Command(
                action, Selection, partial(self.preview_apps, action), self.observe_apps
            )
        return registry

    async def preview_apps(
        self, action: SnapAction, settings: PluginSettings, inputs: Inputs
    ) -> Plan:
        if not isinstance(inputs, Selection):
            raise ValueError("Select applications from the approved catalog")
        return await actions.preview(action, inputs)

    async def observe_apps(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        async with daemon.connection() as local:
            return await actions.readback(local, plan)

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider != "applications":
            return []
        return [
            k for k, v in catalog().items() if query.casefold() in (k + " " + v.title).casefold()
        ]

    async def inspect(self, settings: PluginSettings) -> Result:
        try:
            async with daemon.connection() as local:
                items = await daemon.installed(local, all_revisions=True)
            resources = [
                Resource(
                    kind="snap-revision",
                    identity=f"{item.name}@{item.revision}",
                    before=item.version,
                    after=item.version,
                    ownership=ResourceOwnership.PREEXISTING,
                )
                for item in items
            ]
            return Result(
                outcome=Outcome.NO_CHANGES,
                message=f"{len(items)} installed revision(s).",
                resources=resources,
            )
        except actions.FAILURES:
            return Result(
                outcome=Outcome.NEEDS_USER_ACTION, message="Install snapd and start its service."
            )

    async def plan(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id in (LifecycleAction.CHECK, SnapAction.LIST_INSTALLED):
            result = await self.inspect(settings)
            return Plan(outcome=result.outcome, summary=result.message, resources=result.resources)
        if (
            action_id == LifecycleAction.UNINSTALL
            and (await asyncio.to_thread(installed, "snapd")) is not None
        ):
            try:
                await asyncio.to_thread(require_empty)
            except actions.FAILURES:
                return Plan(
                    outcome=Outcome.BLOCKED,
                    summary="Keep snapd until its snaps, snapshots and active changes are empty.",
                )
        return await super().plan(action_id, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if isinstance(inputs, Selection):
        approval = actions.Approval(action=SnapAction(action_id), selection=inputs, plan=plan)
        run_native_play(
            "snap", "catalog.yml", {"approval": approval.model_dump(mode="json")}, "sudo"
        )
    else:
        if action_id == LifecycleAction.UNINSTALL:
            require_empty()
        apply_packages("snap", action_id, plan)
