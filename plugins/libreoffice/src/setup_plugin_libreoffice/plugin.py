"""Manage LibreOffice through its supported Debian package and native interface."""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from platformdirs import user_data_path
from pydantic import Field
from setup_system_desktop_theme.launchers import LauncherFields, launcher_file
from setup_system_sdk import Inputs, PluginSettings
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.files.assets import PublishedAssets
from setup_system_sdk.files.user_storage import inside_home


class LauncherProfile(LauncherFields, Inputs):
    pass


class PluginAction(StrEnum):
    CONFIGURE_LAUNCHER = "configure-launcher"
    PREFERENCES = "preferences"
    RECOVERY = "recovery"


class Settings(PluginSettings):
    component: Literal["start-center", "writer", "calc", "impress"] = Field(
        default="start-center",
        title="Start component",
        description="Choose the LibreOffice component opened by this launcher.",
    )


class Plugin(PublishedAssets):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = dict.fromkeys(
        (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL),
        "system.packages",
    )
    settings_model = Settings
    file_actions = frozenset({PluginAction.CONFIGURE_LAUNCHER})
    executable = "/usr/bin/libreoffice"
    package_folder = Path(__file__).parent
    manual: ClassVar[dict[StrEnum, str]] = {
        PluginAction.PREFERENCES: "Edit the active LibreOffice profile under Tools → Options.",
        PluginAction.RECOVERY: (
            "Open Tools → Options → Load/Save → General to review AutoRecovery and backup settings."
        ),
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
        LifecycleAction.CHECK: Inputs,
        PluginAction.CONFIGURE_LAUNCHER: LauncherProfile,
        **dict.fromkeys(manual, Inputs),
    }

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(inputs, LauncherProfile) or not isinstance(settings, Settings):
            raise ValueError("Expected a named launcher profile")
        flags = () if settings.component == "start-center" else ("--" + settings.component,)
        return launcher_file(
            inside_home(user_data_path("applications")),
            inputs,
            "libreoffice",
            self.executable,
            flags,
        )
