"""Manage Telegram Desktop through its official strict Snap Store package and native interface."""

import subprocess
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

import httpx
from platformdirs import user_data_path
from pydantic import Field
from setup_system_desktop_theme.launchers import LauncherFields, launcher_file
from setup_system_sdk import Inputs, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction, PackageAction
from setup_system_sdk.files.assets import PublishedAssets
from setup_system_sdk.files.user_storage import inside_home
from setup_system_sdk.providers import CapabilityRequest

from setup_plugin_telegram import snaps
from setup_plugin_telegram.native import run_native_play


class LauncherProfile(LauncherFields, Inputs):
    pass


class TelegramAction(StrEnum):
    CONFIGURE_LAUNCHER = "configure-launcher"
    REMOVE_DEBIAN = "remove-debian-package"
    ACCOUNTS = "accounts"
    NOTIFICATIONS = "notifications"


class Settings(PluginSettings):
    startup: Literal["normal", "tray", "settings"] = Field(
        default="normal",
        title="Launch behavior",
        description="Choose the Telegram window opened by this launcher.",
    )


class Plugin(PublishedAssets):
    delegated_actions: ClassVar[dict[str, str]] = {TelegramAction.REMOVE_DEBIAN: "system.packages"}
    settings_model = Settings
    file_actions = frozenset({TelegramAction.CONFIGURE_LAUNCHER})
    executable = snaps.SNAP
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[StrEnum, str]] = {
        TelegramAction.ACCOUNTS: (
            "Open Telegram Desktop → Settings → Add Account. Complete the native sign-in and "
            "check Active Sessions."
        ),
        TelegramAction.NOTIFICATIONS: (
            "Open Settings → Notifications and Sounds to select desktop notification behavior."
        ),
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        LifecycleAction.INSTALL: Inputs,
        LifecycleAction.UPDATE: Inputs,
        LifecycleAction.UNINSTALL: Inputs,
        LifecycleAction.CHECK: Inputs,
        TelegramAction.CONFIGURE_LAUNCHER: LauncherProfile,
        TelegramAction.REMOVE_DEBIAN: Inputs,
        **dict.fromkeys(manual, Inputs),
    }

    async def inspect(
        self, settings: PluginSettings, context: PluginContext | None = None
    ) -> Result:
        observed = await self.plan(LifecycleAction.CHECK, settings, Inputs(), context)
        return Result(outcome=observed.outcome, message=observed.summary)

    async def plan(
        self,
        action_id: str,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Plan:
        if action_id == TelegramAction.REMOVE_DEBIAN:
            if context is None:
                raise ValueError("Retired package cleanup requires a selected package provider")
            request = CapabilityRequest(
                capability="system.packages",
                step_key="packages",
                payload={
                    "action": PackageAction.REMOVE_RETIRED.value,
                    "consumer": "telegram",
                    "inputs": {"name": snaps.NAME},
                },
            )
            proposal = Plan.model_validate((await context.inspect_provider(request))["proposal"])
            if proposal.outcome is not Outcome.READY:
                return proposal
            step = await context.plan_provider(request)
            if step.resources != tuple(proposal.resources) or step.preconditions.get(
                "proposal"
            ) != proposal.model_dump(mode="json"):
                raise ValueError("The package closure changed during preview")
            return proposal.model_copy(update={"steps": (step,)})
        if action_id in (
            LifecycleAction.INSTALL,
            LifecycleAction.UPDATE,
            LifecycleAction.UNINSTALL,
        ):
            if context is None:
                raise ValueError(
                    "Telegram planning requires the selected platform and package providers"
                )
            platform = await context.inspect_provider(
                CapabilityRequest(capability="system.platform", step_key="platform", payload={})
            )
            inventory = await context.inspect_provider(
                CapabilityRequest(
                    capability="system.packages",
                    step_key="inventory",
                    payload={
                        "consumer": "telegram",
                        "action": LifecycleAction.INSTALL.value,
                        "inventory": [snaps.NAME],
                    },
                )
            )
            arch, installed = platform.get("arch"), inventory.get("installed")
            if (
                not isinstance(arch, str)
                or not isinstance(installed, dict)
                or snaps.NAME not in installed
            ):
                raise ValueError("Platform or package evidence unavailable")
            return await run_sync(
                snaps.prepare,
                snaps.package_action(action_id),
                arch,
                platform.get("supported") is True,
                bool(installed[snaps.NAME]),
            )
        return await super().plan(action_id, settings, inputs, context)

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == LifecycleAction.CHECK:
            try:
                item = snaps.local().get(snaps.NAME)
                if item and item.id == snaps.SNAP_ID:
                    row = snaps.actual(item)
                    return Plan(
                        outcome=Outcome.NO_CHANGES,
                        summary=f"Telegram {row.version} · signed snap revision {row.revision}",
                    )
            except (ValueError, OSError, httpx.HTTPError, subprocess.SubprocessError):
                pass
            return Plan(
                outcome=Outcome.NEEDS_USER_ACTION,
                summary="Inspect the Snap runtime and the official Telegram Desktop installation.",
            )
        return super().prepare(action_id, settings, inputs)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if plan.resources and all(row.kind == "snap" for row in plan.resources):
            return await run_sync(snaps.verify, plan)
        return await super().verify(plan, settings, inputs, context)

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(inputs, LauncherProfile) or not isinstance(settings, Settings):
            raise ValueError("Expected a named launcher profile")
        modes: dict[str, tuple[str, ...]] = {
            "normal": (),
            "tray": ("-startintray",),
            "settings": ("-tosettings",),
        }
        return launcher_file(
            inside_home(user_data_path("applications")),
            inputs,
            "telegram",
            self.executable,
            ("run", snaps.NAME, *modes[settings.startup]),
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    approval = snaps.Approval(action=snaps.package_action(action_id), plan=plan)
    run_native_play("telegram", "snap.yml", approval.model_dump(mode="json"), "sudo")
