"""Manage the Debian snapd runtime and selected applications from the approved Store catalog."""

from functools import partial
from importlib.resources import files as package_files
from pathlib import Path

from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.backend import NativeBackend
from setup_system_sdk.plugin import Command, PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_snap import daemon
from setup_plugin_snap import integration_actions as actions
from setup_plugin_snap.adapters.native import run_native_play
from setup_plugin_snap.adapters.removal_guard import require_empty
from setup_plugin_snap.catalog import Selection, catalog
from setup_plugin_snap.enums import SnapAction


class PluginActions(NativeBackend):
    executable = "/usr/bin/snap"

    package_folder = Path(str(package_files("setup_plugin_snap").joinpath("resources")))

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
        for inventory_action in (SnapAction.LIST_INSTALLED, LifecycleAction.CHECK):
            registry[inventory_action.value] = Command(
                inventory_action,
                Inputs,
                partial(self.plan, inventory_action),
                self.observe_inventory,
            )
        return registry

    async def observe_inventory(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        current = await self.inspect(settings)
        if current.outcome is not Outcome.NO_CHANGES:
            return current
        return Result.observed(
            outcome=Outcome.NO_CHANGES if current.resources == plan.resources else Outcome.PARTIAL,
            message="Checked the installed Snap revision inventory.",
            resources=current.resources,
        )

    async def preview_apps(
        self,
        action: SnapAction,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if not isinstance(inputs, Selection):
            raise ValueError("Select applications from the approved catalog")
        return await actions.preview(action, inputs, context)

    async def observe_apps(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        async with daemon.connection() as local:
            return await actions.readback(local, plan)

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        if provider != "applications":
            return []
        return [
            k for k, v in catalog().items() if query.casefold() in (k + " " + v.title).casefold()
        ]

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
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

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if action_id in (LifecycleAction.CHECK, SnapAction.LIST_INSTALLED):
            result = await self.inspect(settings)
            return Plan(outcome=result.outcome, summary=result.message, resources=result.resources)
        if action_id == LifecycleAction.UNINSTALL and context:
            proposal = await super().plan(action_id, settings, inputs, context)
            if any(r.identity == "snapd" and r.before for r in proposal.resources):
                try:
                    await run_sync(require_empty)
                except actions.FAILURES:
                    return Plan(
                        outcome=Outcome.BLOCKED,
                        summary="Keep snapd until its snaps, snapshots and changes are empty.",
                    )
            return proposal
        return await super().plan(action_id, settings, inputs, context)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if isinstance(inputs, Selection):
        approval = actions.Approval(action=SnapAction(action_id), selection=inputs, plan=plan)
        run_native_play(
            "snap", "catalog.yml", {"approval": approval.model_dump(mode="json")}, "sudo"
        )
        return
    raise ValueError("Package operations require their confirmed provider step")
