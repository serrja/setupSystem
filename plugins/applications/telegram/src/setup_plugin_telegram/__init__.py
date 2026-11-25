"""Manage Telegram Desktop through its supported Debian package and native interface."""

from pathlib import Path
from typing import ClassVar

from setup_system_sdk import Inputs, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages


class Plugin(DebianPackages):
    executable = "/usr/bin/telegram-desktop"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[str, str]] = {
        "accounts": (
            "Open Telegram Desktop → Settings → Add Account. Complete the native sign-in and "
            "check Active Sessions."
        ),
        "notifications": (
            "Open Settings → Notifications and Sounds to select desktop notification behavior."
        ),
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        **dict.fromkeys(manual, Inputs),
    }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("telegram", action_id, plan)
