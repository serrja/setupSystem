"""Manage Telegram Desktop through its supported Debian package and native interface."""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from setup_system_sdk import Inputs, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.managed_assets import PublishedAssets
from setup_system_sdk.native import LauncherProfile, launcher_file


class Settings(PluginSettings):
    startup: Literal["normal", "tray", "settings"] = Field(
        default="normal", title="Launch behavior"
    )


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    file_actions = frozenset({"configure-launcher"})
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
        "configure-launcher": LauncherProfile,
        **dict.fromkeys(manual, Inputs),
    }

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(inputs, LauncherProfile) or not isinstance(settings, Settings):
            raise ValueError("Expected a named launcher profile")
        modes: dict[str, tuple[str, ...]] = {
            "normal": (),
            "tray": ("-startintray",),
            "settings": ("-tosettings",),
        }
        return launcher_file(inputs, "telegram", self.executable, modes[settings.startup])


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("telegram", action_id, plan)
