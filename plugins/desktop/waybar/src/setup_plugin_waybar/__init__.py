"""Waybar workstation configuration and Debian packages."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import Field

from setup_system.infrastructure.palette import desktop_style
from setup_system.layout import resource_root
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
    compact: bool = Field(default=True, title="Compact top panel")
    opacity: float = Field(default=0.96, ge=0.75, le=1, allow_inf_nan=False, title="Panel opacity")
    rounding: int = Field(default=12, ge=0, le=24, title="Panel corner radius")


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    executable = "/usr/bin/waybar"
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
            raise ValueError("Expected Waybar settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context()
        style = desktop_style(resource_root())
        context.update(
            panel_height=style.compact_panel_height if settings.compact else style.panel_height,
            opacity=settings.opacity,
            rounding=settings.rounding,
        )
        return {
            directory / name: render_desktop(FOLDER / "assets" / name, context)
            for name in ("waybar.json", "style.css", "panel.py", "brightness.sh")
        }


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("waybar", action_id, plan)
