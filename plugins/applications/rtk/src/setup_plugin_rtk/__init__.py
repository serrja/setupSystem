"""Resolve an official RTK release and verify its binary after publication."""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import ClassVar

from setup_plugin_rtk.runtime import _released_binary, install_rtk, plan_rtk, release
from setup_system.infrastructure.file_publication import observe
from setup_system_sdk import (
    ActionEvent,
    Inputs,
    LifecycleAction,
    Outcome,
    Plan,
    PluginSettings,
    Resource,
    Result,
)
from setup_system_sdk.native import NativeBackend, owned_file_removal, remove_owned_file


class Plugin(NativeBackend):
    executable = "/usr/bin/rtk"
    inputs: ClassVar[dict[str, type[Inputs]]] = dict.fromkeys(
        (
            LifecycleAction.INSTALL,
            LifecycleAction.UPDATE,
            LifecycleAction.CHECK,
            LifecycleAction.UNINSTALL,
        ),
        Inputs,
    )

    async def inspect(self, settings: PluginSettings) -> Result:
        plan = await self.plan("check", settings, Inputs())
        return Result(outcome=plan.outcome, message=plan.summary)

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == LifecycleAction.UNINSTALL:
            return owned_file_removal(Path.home() / ".local/bin/rtk")
        selected = release()
        status, summary = plan_rtk(selected)
        target = status.path or Path.home() / ".local/bin/rtk"
        if action_id == LifecycleAction.CHECK:
            return Plan(
                outcome=Outcome.NO_CHANGES if status.version else Outcome.BLOCKED, summary=summary
            )
        if status.path and (not status.product_matches or not target.is_relative_to(Path.home())):
            return Plan(outcome=Outcome.BLOCKED, summary="Existing RTK is externally managed")
        if (
            status.path
            and status.version != selected.version
            and action_id != LifecycleAction.UPDATE
        ):
            return Plan(outcome=Outcome.BLOCKED, summary="Use Update for the existing executable")
        after = hashlib.sha256(_released_binary(selected)).hexdigest()
        original = observe(target)
        return Plan(
            summary=f"RTK {selected.version}: {summary}",
            resources=[
                Resource(
                    kind="file",
                    identity=str(target),
                    ownership="modified" if original else "created",
                    before=original.sha256 if original else None,
                    before_permissions=original.mode if original else None,
                    after=after,
                    permissions=0o755,
                    cleanup=original is None,
                )
            ],
            outcome=Outcome.NO_CHANGES if status.version == selected.version else Outcome.READY,
        )

    async def install(self, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result:
        return await asyncio.to_thread(self.mutate, "install", settings, inputs, plan)

    async def uninstall(self, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result:
        return await asyncio.to_thread(remove_owned_file, Path.home() / ".local/bin/rtk", plan)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id == LifecycleAction.UNINSTALL:
            return remove_owned_file(Path.home() / ".local/bin/rtk", plan)
        (row,) = plan.resources
        target = Path(row.identity)
        current = observe(target)
        selected = release()
        if (
            hashlib.sha256(_released_binary(selected)).hexdigest() != row.after
            or (current.sha256 if current else None) != row.before
            or (current.mode if current else None) != row.before_permissions
        ):
            return Result(outcome=Outcome.BLOCKED, message="Release or target changed")
        message, folder = install_rtk(
            selected, update=action_id == LifecycleAction.UPDATE, reviewed={target: current}
        )
        return Result(
            outcome=Outcome.CHANGED,
            events=(
                ActionEvent(
                    phase="applied",
                    message_code="release_binary_published",
                    details={"resource_count": 1},
                ),
            ),
            message=message,
            recovery_id=folder.name if folder else None,
            resources=plan.resources,
        )

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        (row,) = plan.resources
        target = Path(row.identity)
        state = await asyncio.to_thread(observe, target)
        ok = (
            state is None
            if row.effect == "remove"
            else bool(
                state
                and state.sha256 == row.after
                and state.mode == row.permissions
                and os.access(target, os.X_OK)
            )
        )
        return Result(
            outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
            message="RTK binary matches the approved release" if ok else "RTK binary differs",
            resources=plan.resources if ok else [],
        )
