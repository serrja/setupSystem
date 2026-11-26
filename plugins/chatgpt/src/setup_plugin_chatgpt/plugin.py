"""Manage ChatGPT through its supported Debian package and native interface."""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from setup_plugin_chatgpt.enums import PluginAction
from setup_system.execution.recipes.apt import DebianPackages, apply_packages
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan


class Plugin(DebianPackages):
    executable = "/usr/bin/chatgpt"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[StrEnum, str]] = {
        PluginAction.ACCOUNT: "Sign in from the official ChatGPT account dialog.",
        PluginAction.SETTINGS: "Open ChatGPT Settings for personalization and account preferences.",
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        **dict.fromkeys(manual, Inputs),
    }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("chatgpt", action_id, plan)
