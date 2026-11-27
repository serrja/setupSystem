"""Publish a local Neovim configuration with explicit language and clipboard selections."""

import json
import os
import tomllib
from importlib.resources import files as package_files
from pathlib import Path

from setup_system_desktop_theme.rendering import desktop_context, render_desktop
from setup_system_sdk.blocking import run_sync
from setup_system_sdk.context import PluginContext
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.files.assets import (
    PublishedAssets,
    desktop_directories,
)
from setup_system_sdk.files.user_storage import inside_home
from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan, Result

from setup_plugin_neovim import extensions
from setup_plugin_neovim.enums import PluginAction
from setup_plugin_neovim.models import Search, Settings


def lua_list(values: list[str]) -> str:
    return "{" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "}"


class PluginActions(PublishedAssets):
    executable = "/usr/bin/nvim"

    package_folder = Path(str(package_files("setup_plugin_neovim").joinpath("resources")))

    file_actions = frozenset({LifecycleAction.CONFIGURE})

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        if action_id == PluginAction.REMOVE_PARSERS:
            return extensions.parser_removal()
        if action_id == PluginAction.INSTALL_PLUGINS:
            return extensions.plan_plugins()
        if action_id == PluginAction.INSTALL_PARSERS and isinstance(settings, Settings):
            return extensions.parser_plan(list(settings.languages))
        if action_id == PluginAction.VERIFY_EDITOR and isinstance(settings, Settings):
            return Plan(summary="Run the offline headless editor check in a temporary project")
        return super().prepare(action_id, settings, inputs)

    def mutate(
        self, action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan
    ) -> Result:
        if action_id == PluginAction.REMOVE_PARSERS:
            return extensions.remove_parsers(plan)
        if action_id == PluginAction.INSTALL_PLUGINS:
            return extensions.update(plan)
        if action_id == PluginAction.INSTALL_PARSERS and isinstance(settings, Settings):
            return extensions.install_parsers(plan, list(settings.languages))
        if action_id == PluginAction.VERIFY_EDITOR and isinstance(settings, Settings):
            return extensions.verify_parsers(plan, list(settings.languages))
        return super().mutate(action_id, settings, inputs, plan)

    async def verify(
        self,
        plan: Plan,
        settings: PluginSettings,
        inputs: Inputs,
        context: PluginContext | None = None,
    ) -> Result:
        if plan.resources and all(r.kind == "git-checkout" for r in plan.resources):
            return await run_sync(extensions.verify_plugins, plan)
        if (
            plan.resources
            and all(r.kind == "parser" for r in plan.resources)
            and isinstance(settings, Settings)
        ):
            if all(row.effect == "remove" for row in plan.resources):
                absent = await run_sync(
                    lambda: all(not os.path.lexists(row.identity) for row in plan.resources)
                )
                return Result.observed(
                    outcome=Outcome.COMPLETE if absent else Outcome.PARTIAL,
                    message="Checked the selected parser files",
                    resources=plan.resources,
                )
            return await run_sync(extensions.verify_parsers, plan, list(settings.languages))
        if not plan.resources and isinstance(settings, Settings):
            return await run_sync(extensions.verify_parsers, plan, list(settings.languages))
        return await super().verify(plan, settings, inputs)

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Unexpected editor settings")
        root = inside_home(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))
        resources = self.package_folder
        catalog = tomllib.loads((resources / "editor.toml").read_text())
        template = (resources / "nvim-init.lua").read_text()
        init = (
            template.replace("@{ plugin_lua }@", lua_list(list(catalog["plugins"])))
            .replace(
                "@{ editor_catalog.search | to_json | editor_globs_lua }@",
                lua_list(Search.model_validate(catalog["search"]).exclude_globs),
            )
            .replace("@{ render_ctx.selected_lua }@", lua_list(list(settings.languages)))
        )
        if "@{" in init:
            raise ValueError("Neovim resource contains an unresolved template field")
        desired = {root / "nvim" / "init.lua": init.encode()}
        palette = desktop_context(desktop_directories())
        for path in sorted((resources / "nvim").rglob("*.lua")):
            desired[root / "nvim" / path.relative_to(resources / "nvim")] = render_desktop(
                path, palette
            )
        if not settings.clipboard:
            path = root / "nvim" / "lua/config/options.lua"
            desired[path] += b'\nvim.opt.clipboard = ""\n'
        if settings.primary_editor:
            desired[root / "environment.d" / "90-setup-system-editor.conf"] = (
                resources / "editor-env.conf"
            ).read_bytes()
        return desired
