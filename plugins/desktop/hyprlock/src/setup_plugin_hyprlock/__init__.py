"""Hyprlock workstation configuration and Debian packages."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import Field

from setup_system_sdk import Inputs, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.managed_assets import (
    PublishedAssets,
    desktop_context,
    desktop_directories,
    render_desktop,
)

FOLDER = Path(__file__).parent


class Settings(PluginSettings):
    fade_on_empty: bool = Field(default=False, title="Fade empty password field")


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    executable = "/usr/bin/hyprlock"
    package_folder = FOLDER
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        "check": Inputs,
        "configure": Inputs,
    }
    file_actions = frozenset(("configure",))

    def mode(self, path: Path) -> int:
        return 0o700 if path.suffix in {".sh", ".py"} or path.name == "start-hyprland" else 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Expected Hyprlock settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context()
        context["fade_on_empty"] = "true" if settings.fade_on_empty else "false"
        return {
            directory / "hyprlock.conf": render_desktop(FOLDER / "assets/hyprlock.conf", context)
        }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("hyprlock", action_id, plan)
