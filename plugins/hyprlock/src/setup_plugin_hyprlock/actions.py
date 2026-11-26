"""Hyprlock workstation configuration and Debian packages."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources import files as package_files
from pathlib import Path

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.files.assets import (
    PublishedAssets,
    desktop_context,
    desktop_directories,
    render_desktop,
)
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_hyprlock.models import Settings

FOLDER = Path(str(package_files("setup_plugin_hyprlock").joinpath("resources")))


class PluginActions(PublishedAssets):
    executable = "/usr/bin/hyprlock"

    package_folder = FOLDER

    file_actions = frozenset((LifecycleAction.CONFIGURE,))

    def mode(self, path: Path) -> int:
        return 0o700 if path.suffix in {".sh", ".py"} or path.name == "start-hyprland" else 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Expected Hyprlock settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context(Path(str(files("setup_plugin_hyprlock") / "resources")))
        context["fade_on_empty"] = "true" if settings.fade_on_empty else "false"
        return {
            directory / "hyprlock.conf": render_desktop(FOLDER / "assets/hyprlock.conf", context)
        }
