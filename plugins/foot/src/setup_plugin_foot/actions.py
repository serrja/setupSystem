"""Foot workstation configuration and Debian packages."""

from __future__ import annotations

from importlib.resources import files as package_files
from pathlib import Path

from setup_system_desktop_theme.palette import desktop_style
from setup_system_desktop_theme.rendering import desktop_context, render_desktop
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.files.assets import (
    PublishedAssets,
    desktop_directories,
)
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_foot.models import Settings

FOLDER = Path(str(package_files("setup_plugin_foot").joinpath("resources")))


class PluginActions(PublishedAssets):
    executable = "/usr/bin/foot"

    package_folder = FOLDER

    file_actions = frozenset((LifecycleAction.CONFIGURE,))

    def mode(self, path: Path) -> int:
        return 0o700 if path.suffix in {".sh", ".py"} or path.name == "start-hyprland" else 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Expected Foot settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context(paths)
        fonts = dict(desktop_style().fonts.model_dump())
        fonts["terminal_size"] = settings.font_size
        context["fonts"] = fonts
        return {directory / "foot.ini": render_desktop(FOLDER / "assets/foot.ini", context)}
