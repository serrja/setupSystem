"""Manage Telegram Desktop through its official strict Snap Store package and native interface."""

import asyncio
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

import httpx
from pydantic import Field

from setup_plugin_telegram import snaps
from setup_system_sdk import Inputs, LifecycleAction, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.apt import apply_packages, uninstall_plan, verify_packages
from setup_system_sdk.managed_assets import PublishedAssets
from setup_system_sdk.native import LauncherProfile, launcher_file
from setup_system_sdk.native_play import run_native_play


class TelegramAction(StrEnum):
    CONFIGURE_LAUNCHER = "configure-launcher"
    REMOVE_DEBIAN = "remove-debian-package"
    ACCOUNTS = "accounts"
    NOTIFICATIONS = "notifications"


class Settings(PluginSettings):
    startup: Literal["normal", "tray", "settings"] = Field(
        default="normal", title="Launch behavior"
    )


class Plugin(PublishedAssets):
    settings_model = Settings
    file_actions = frozenset({TelegramAction.CONFIGURE_LAUNCHER})
    executable = snaps.SNAP
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[str, str]] = {
        TelegramAction.ACCOUNTS: (
            "Open Telegram Desktop → Settings → Add Account. Complete the native sign-in and "
            "check Active Sessions."
        ),
        TelegramAction.NOTIFICATIONS: (
            "Open Settings → Notifications and Sounds to select desktop notification behavior."
        ),
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        LifecycleAction.INSTALL: Inputs,
        LifecycleAction.UPDATE: Inputs,
        LifecycleAction.UNINSTALL: Inputs,
        LifecycleAction.CHECK: Inputs,
        TelegramAction.CONFIGURE_LAUNCHER: LauncherProfile,
        TelegramAction.REMOVE_DEBIAN: Inputs,
        **dict.fromkeys(manual, Inputs),
    }

    async def inspect(self, settings: PluginSettings) -> Result:
        observed = await self.plan(LifecycleAction.CHECK, settings, Inputs())
        return Result(outcome=observed.outcome, message=observed.summary)

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == TelegramAction.REMOVE_DEBIAN:
            return uninstall_plan(self.package_folder)
        if action_id in {
            LifecycleAction.INSTALL,
            LifecycleAction.UPDATE,
            LifecycleAction.UNINSTALL,
        }:
            return snaps.prepare(snaps.package_action(action_id))
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

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources and all(row.kind == "package" for row in plan.resources):
            return await asyncio.to_thread(verify_packages, plan)
        if plan.resources and all(row.kind == "snap" for row in plan.resources):
            if any(row.ownership == "created" and row.effect == "ensure" for row in plan.resources):
                return Result(
                    outcome=Outcome.NEEDS_USER_ACTION,
                    message="Inspect snap changes before claiming ownership after interruption.",
                )
            return await asyncio.to_thread(snaps.verify, plan)
        return await super().verify(plan, settings, inputs)

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(inputs, LauncherProfile) or not isinstance(settings, Settings):
            raise ValueError("Expected a named launcher profile")
        modes: dict[str, tuple[str, ...]] = {
            "normal": (),
            "tray": ("-startintray",),
            "settings": ("-tosettings",),
        }
        return launcher_file(
            inputs, "telegram", self.executable, ("run", snaps.NAME, *modes[settings.startup])
        )


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> Result:
    if action_id == TelegramAction.REMOVE_DEBIAN:
        apply_packages("telegram", LifecycleAction.UNINSTALL, plan)
        return verify_packages(plan)
    approval = snaps.Approval(action=snaps.package_action(action_id), plan=plan)
    run_native_play("telegram", "snap.yml", approval.model_dump(mode="json"), "sudo")
    return snaps.verify(plan)
