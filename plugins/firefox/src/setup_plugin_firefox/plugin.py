"""Firefox ESR package management and system-wide enterprise policy."""

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from setup_plugin_firefox import policy
from setup_plugin_firefox.enums import PolicyAction
from setup_plugin_firefox.settings import Settings
from setup_system.execution.recipes.apt import DebianPackages, apply_packages
from setup_system.execution.recipes.native_play import run_native_play
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result


class Plugin(DebianPackages):
    executable = "/usr/bin/firefox-esr"
    settings_model = Settings
    package_folder = Path(__file__).parent
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: Inputs,
        PolicyAction.REMOVE_POLICY: Inputs,
    }

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == PolicyAction.REMOVE_POLICY:
            return policy.removal()
        if action_id == LifecycleAction.CONFIGURE and isinstance(settings, Settings):
            return policy.preview(settings)
        return super().prepare(action_id, settings, inputs)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources[0].kind == "file" and isinstance(settings, Settings):
            if plan.resources[0].effect == "remove":
                data, _ = await asyncio.to_thread(policy.snapshot, policy.PATH)
                return Result.observed(
                    outcome=Outcome.COMPLETE if data is None else Outcome.PARTIAL,
                    message="Checked the removed Firefox policy",
                    resources=plan.resources,
                )
            checked = await asyncio.to_thread(policy.preview, settings)
            ok = (
                checked.outcome == Outcome.NO_CHANGES
                and checked.resources[0].before == plan.resources[0].after
            )
            return Result.observed(
                outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
                message="Firefox policy verified" if ok else "Inspect Firefox policy state",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id in (LifecycleAction.CONFIGURE, PolicyAction.REMOVE_POLICY) and isinstance(
        settings, Settings
    ):
        approved = policy.Approval.model_validate(
            {"action_id": action_id, "settings": settings, "plan": plan}
        )
        run_native_play(
            "firefox", "policy.yml", {"approval": approved.model_dump(mode="json")}, "sudo"
        )
    else:
        apply_packages("firefox", action_id, plan)
