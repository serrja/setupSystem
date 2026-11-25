"""Manage LibreOffice through its supported Debian package and native interface."""

from pathlib import Path
from typing import ClassVar

from setup_system_sdk import Inputs, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages


class Plugin(DebianPackages):
    executable = "/usr/bin/libreoffice"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[str, str]] = {
        "preferences": "Open Tools → Options to edit the active LibreOffice user profile.",
        "recovery": (
            "Open Tools → Options → Load/Save → General to review AutoRecovery and backup settings."
        ),
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        **dict.fromkeys(manual, Inputs),
    }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("libreoffice", action_id, plan)
