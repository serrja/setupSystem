"""Hyprlock workstation configuration and Debian packages."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from setup_plugin_hyprlock.settings import Settings
from setup_system.execution.recipes.apt import DebianPackages, apply_packages
from setup_system.execution.recipes.managed_assets import (
    PublishedAssets,
    desktop_context,
    desktop_directories,
    render_desktop,
)
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan

FOLDER = Path(__file__).parent


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    executable = "/usr/bin/hyprlock"
    package_folder = FOLDER
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: Inputs,
    }
    file_actions = frozenset((LifecycleAction.CONFIGURE,))

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
