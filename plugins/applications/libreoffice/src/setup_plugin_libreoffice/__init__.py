"""Manage LibreOffice through its supported Debian package and native interface."""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_system_sdk import Inputs, LifecycleAction, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.managed_assets import PublishedAssets
from setup_system_sdk.native import LauncherProfile, launcher_file


class PluginAction(StrEnum):
    CONFIGURE_LAUNCHER = "configure-launcher"
    PREFERENCES = "preferences"
    RECOVERY = "recovery"


class Settings(PluginSettings):
    component: Literal["start-center", "writer", "calc", "impress"] = Field(
        default="start-center", title="Start component"
    )


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    file_actions = frozenset({PluginAction.CONFIGURE_LAUNCHER})
    executable = "/usr/bin/libreoffice"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[str, str]] = {
        PluginAction.PREFERENCES: "Edit the active LibreOffice profile under Tools → Options.",
        PluginAction.RECOVERY: (
            "Open Tools → Options → Load/Save → General to review AutoRecovery and backup settings."
        ),
    }
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        PluginAction.CONFIGURE_LAUNCHER: LauncherProfile,
        **dict.fromkeys(manual, Inputs),
    }

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(inputs, LauncherProfile) or not isinstance(settings, Settings):
            raise ValueError("Expected a named launcher profile")
        flags = () if settings.component == "start-center" else ("--" + settings.component,)
        return launcher_file(inputs, "libreoffice", self.executable, flags)


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("libreoffice", action_id, plan)
