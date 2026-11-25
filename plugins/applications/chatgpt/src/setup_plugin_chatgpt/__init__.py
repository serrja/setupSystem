"""Manage ChatGPT through its supported Debian package and native interface."""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from setup_system_sdk import Inputs, LifecycleAction, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages


class PluginAction(StrEnum):
    ACCOUNT = "account"
    SETTINGS = "settings"


class Plugin(DebianPackages):
    executable = "/usr/bin/chatgpt"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[str, str]] = {
        PluginAction.ACCOUNT: "Sign in from the official ChatGPT account dialog.",
        PluginAction.SETTINGS: "Open ChatGPT Settings for personalization and account preferences.",
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        **dict.fromkeys(manual, Inputs),
    }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("chatgpt", action_id, plan)
