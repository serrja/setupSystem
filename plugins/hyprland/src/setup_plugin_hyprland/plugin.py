"""Hyprland workstation configuration and Debian packages."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from setup_plugin_hyprland.enums import PluginAction
from setup_plugin_hyprland.settings import Settings
from setup_system.execution.recipes import xkb
from setup_system.execution.recipes.apt import DebianPackages, apply_packages
from setup_system.execution.recipes.managed_assets import (
    PublishedAssets,
    desktop_context,
    desktop_directories,
    render_desktop,
)
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from .displays import discover_outputs, validate_layouts
from .models import AdvertisedMode, MonitorLayout


class Display(Inputs):
    output: str = Field(
        description="Select a display connector reported by the current compositor session.",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "outputs"}},
    )
    mode: AdvertisedMode = Field(
        description="Choose an advertised display mode for the selected connector.",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "modes"}},
    )
    x: int = Field(
        description="Set the horizontal position of this output in the desktop layout.",
        ge=-65535,
        le=65535,
        default=0,
    )
    y: int = Field(
        description="Set the vertical position of this output in the desktop layout.",
        ge=-65535,
        le=65535,
        default=0,
    )
    scale: float = Field(
        description="Set the logical display scale for this output.",
        ge=0.25,
        le=8.0,
        allow_inf_nan=False,
        default=1.0,
    )


FOLDER = Path(__file__).parent


class Plugin(DebianPackages, PublishedAssets):
    settings_model = Settings
    executable = "/usr/bin/Hyprland"
    package_folder = FOLDER
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: Inputs,
        PluginAction.SAVE_MONITOR: Display,
        PluginAction.TEST_MONITOR: Display,
    }
    file_actions = frozenset(
        (
            LifecycleAction.CONFIGURE,
            PluginAction.SAVE_MONITOR,
        )
    )

    def mode(self, path: Path) -> int:
        return 0o700 if path.suffix in {".sh", ".py"} or path.name == "start-hyprland" else 0o600

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Expected Hyprland settings.")
        paths = desktop_directories()
        directory = paths["config"] / "hypr/setup-system"
        context = desktop_context()
        if action_id == PluginAction.SAVE_MONITOR:
            if not isinstance(inputs, Display):
                raise ValueError("Expected a reviewed monitor layout.")
            layout = MonitorLayout.model_validate(inputs.model_dump())
            validate_layouts((layout,))
            return {directory / "monitors.lua": (layout.rule() + "\n").encode()}
        xkb.validate_layouts(settings.keyboard)
        context.update(
            effects="true" if settings.effects else "false",
            rounding=settings.rounding,
            keyboard=",".join(settings.keyboard),
        )
        assets = FOLDER / "assets"
        result = {
            directory / name: render_desktop(assets / name, context)
            for name in (
                "profile.lua",
                "hypridle.conf",
                "keys.txt",
                "mako.conf",
                "power.sh",
                "session.sh",
                "screenshot.sh",
                "wallpaper.svg",
                "wofi.conf",
            )
        }
        for name, dest in {
            "start-hyprland.sh": paths["data"] / "setup-system/bin/start-hyprland",
            "hyprland.lua": paths["config"] / "hypr/hyprland.lua",
            "hyprland-portals.conf": paths["config"] / "xdg-desktop-portal/hyprland-portals.conf",
            "60-setup-system-fonts.conf": paths["config"]
            / "fontconfig/conf.d/60-setup-system-fonts.conf",
            "hypr-keyboard.lua": directory / "keyboard.lua",
        }.items():
            result[dest] = render_desktop(assets / name, context)
        return result

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id != PluginAction.TEST_MONITOR:
            return super().prepare(action_id, settings, inputs)
        if not isinstance(inputs, Display):
            raise ValueError("Select a connected output and mode.")
        selected = MonitorLayout.model_validate(inputs.model_dump())
        (original,) = validate_layouts((selected,))
        if original == selected:
            return Plan(outcome=Outcome.NO_CHANGES, summary="Output already uses this mode.")
        return Plan(
            summary=f"Test {selected.output} at {selected.mode} for twelve seconds.",
            warnings=["The output may go dark temporarily. The original layout will be restored."],
            resources=[
                Resource(
                    kind="display",
                    identity=selected.output,
                    before=original.model_dump_json(),
                    after=original.model_dump_json(),
                    ownership=ResourceOwnership.MODIFIED,
                )
            ],
        )

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id != PluginAction.TEST_MONITOR:
            return super().mutate(action_id, settings, inputs, plan)
        subprocess.run(
            [sys.executable, "-I", "-B", "-m", "setup_plugin_hyprland.displays"],
            input=inputs.model_dump_json().encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return Result(
            outcome=Outcome.COMPLETE, message="Temporary test finished.", resources=plan.resources
        )

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources and plan.resources[0].kind == "display":
            row = plan.resources[0]
            facts = await asyncio.to_thread(discover_outputs)
            restored = any(
                output.current.output == row.identity
                and output.current.model_dump_json() == row.after
                for output in facts
            )
            return Result.observed(
                outcome=Outcome.COMPLETE if restored else Outcome.PARTIAL,
                message="Original output mode restored."
                if restored
                else "Inspect the output layout.",
                resources=plan.resources,
            )
        return await super().verify(plan, settings, inputs)

    async def choices(self, provider: str, query: str, settings: PluginSettings) -> list[str]:
        if provider == "keyboard":
            names = await asyncio.to_thread(xkb.keyboard_layouts)
            return [name for name in names if query.casefold() in name][:128]
        if provider not in ("outputs", "modes"):
            return []
        outputs = await asyncio.to_thread(discover_outputs)
        options = (
            {x.current.output for x in outputs}
            if provider == "outputs"
            else {mode for x in outputs for mode in x.modes}
        )
        return sorted(x for x in options if query.casefold() in x.casefold())[:128]


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("hyprland", action_id, plan)
