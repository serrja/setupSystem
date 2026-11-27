"""Hyprland workstation configuration and Debian packages."""

from __future__ import annotations

import sys
from importlib.resources import files as package_files
from pathlib import Path

from setup_system_desktop_theme.rendering import desktop_context, render_desktop
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.assets import (
    PublishedAssets,
    desktop_directories,
)
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.processes import run_command
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Resource, Result

from setup_plugin_hyprland.adapters import xkb
from setup_plugin_hyprland.enums import PluginAction
from setup_plugin_hyprland.models import Display
from setup_plugin_hyprland.settings import Settings

from .displays import discover_outputs, validate_layouts
from .models import MonitorLayout

FOLDER = Path(str(package_files("setup_plugin_hyprland").joinpath("resources")))


class PluginActions(PublishedAssets):
    executable = "/usr/bin/Hyprland"

    package_folder = FOLDER

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
        context = desktop_context(paths)
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
        run_command(
            [sys.executable, "-I", "-B", "-m", "setup_plugin_hyprland.displays"],
            input=inputs.model_dump_json(),
            discard_output=True,
            check=True,
        )
        return Result(
            outcome=Outcome.COMPLETE, message="Temporary test finished.", resources=plan.resources
        )

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if plan.resources and plan.resources[0].kind == "display":
            row = plan.resources[0]
            facts = await run_sync(discover_outputs)
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

    async def choices(
        self,
        provider: str,
        query: str,
        settings: PluginSettings,
        context: PluginContext | None = None,
    ) -> list[str]:
        if provider == "keyboard":
            names = await run_sync(xkb.keyboard_layouts)
            return [name for name in names if query.casefold() in name][:128]
        if provider not in ("outputs", "modes"):
            return []
        outputs = await run_sync(discover_outputs)
        options = (
            {x.current.output for x in outputs}
            if provider == "outputs"
            else {mode for x in outputs for mode in x.modes}
        )
        return sorted(x for x in options if query.casefold() in x.casefold())[:128]
