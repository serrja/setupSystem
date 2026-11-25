"""Manage ChatGPT through its supported Debian package and native interface."""

from pathlib import Path
from typing import ClassVar

from setup_system_sdk import Inputs, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages


class Plugin(DebianPackages):
    executable = "/usr/bin/chatgpt"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[str, str]] = {
        "account": "Open the official ChatGPT application and sign in using its account dialog.",
        "settings": "Open ChatGPT Settings to manage personalization and account preferences.",
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        **dict.fromkeys(manual, Inputs),
    }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("chatgpt", action_id, plan)
