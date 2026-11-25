"""Firefox ESR package management and system-wide enterprise policy."""

import asyncio
from pathlib import Path
from typing import ClassVar

from setup_plugin_firefox import policy
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native_play import run_native_play


class Plugin(DebianPackages):
    executable = "/usr/bin/firefox-esr"
    settings_model = policy.Settings
    package_folder = Path(__file__).parent
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "configure": Inputs,
        "remove-policy": Inputs,
    }

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == "remove-policy":
            return policy.removal()
        if action_id == "configure" and isinstance(settings, policy.Settings):
            return policy.preview(settings)
        return super().prepare(action_id, settings, inputs)

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources[0].kind == "file" and isinstance(settings, policy.Settings):
            if plan.resources[0].effect == "remove":
                data, _ = await asyncio.to_thread(policy.snapshot, policy.PATH)
                return Result(
                    outcome=Outcome.COMPLETE if data is None else Outcome.PARTIAL,
                    message="Checked the removed Firefox policy",
                    resources=plan.resources,
                )
            checked = await asyncio.to_thread(policy.preview, settings)
            ok = (
                checked.outcome == Outcome.NO_CHANGES
                and checked.resources[0].before == plan.resources[0].after
            )
            return Result(
                outcome=Outcome.COMPLETE if ok else Outcome.PARTIAL,
                message="Firefox policy verified" if ok else "Inspect Firefox policy state",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    if action_id in ("configure", "remove-policy") and isinstance(settings, policy.Settings):
        approved = policy.Approval(
            action_id="configure" if action_id == "configure" else "remove-policy",
            settings=settings,
            plan=plan,
        )
        run_native_play(
            "firefox", "policy.yml", {"approval": approved.model_dump(mode="json")}, "sudo"
        )
    else:
        apply_packages("firefox", action_id, plan)
