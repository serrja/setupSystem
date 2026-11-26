"""Waybar workstation configuration and Debian packages."""

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
from setup_system_sdk.files.palette import desktop_style
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_waybar.models import Settings

FOLDER = Path(str(package_files("setup_plugin_waybar").joinpath("resources")))


class PluginActions(PublishedAssets):
    executable = "/usr/bin/waybar"

    package_folder = FOLDER

    file_actions = frozenset((LifecycleAction.CONFIGURE,))

    def mode(self, path: Path) -> int:
        return 0o700 if path.suffix in {".sh", ".py"} or path.name == "start-hyprland" else 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Expected Waybar settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context(Path(str(files("setup_plugin_waybar") / "resources")))
        style = desktop_style(Path(str(files("setup_plugin_waybar") / "resources")))
        context.update(
            panel_height=style.compact_panel_height if settings.compact else style.panel_height,
            opacity=settings.opacity,
            rounding=settings.rounding,
        )
        return {
            directory / name: render_desktop(FOLDER / "assets" / name, context)
            for name in ("waybar.json", "style.css", "panel.py", "brightness.sh")
        }
