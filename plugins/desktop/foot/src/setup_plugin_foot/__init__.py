"""Foot workstation configuration and Debian packages."""

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
    font_size: int = Field(
        default=11,
        ge=6,
        le=48,
        title="Terminal font size",
        json_schema_extra={"ui": {"units": "pt"}},
    )


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    executable = "/usr/bin/foot"
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
            raise ValueError("Expected Foot settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context()
        fonts = dict(desktop_style(resource_root()).fonts.model_dump())
        fonts["terminal_size"] = settings.font_size
        context["fonts"] = fonts
        return {directory / "foot.ini": render_desktop(FOLDER / "assets/foot.ini", context)}


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("foot", action_id, plan)
