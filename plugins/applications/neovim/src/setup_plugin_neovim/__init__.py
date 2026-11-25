"""Publish a local Neovim profile with explicit language and clipboard selections."""

import asyncio
import json
import os
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, field_validator

from setup_plugin_neovim import extensions
from setup_system.infrastructure.user_storage import inside_home
from setup_system_sdk import Inputs, LifecycleAction, Outcome, Plan, PluginSettings, Result
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.managed_assets import PublishedAssets, desktop_context, render_desktop


class PluginAction(StrEnum):
    INSTALL_PARSERS = "install-parsers"
    INSTALL_PLUGINS = "install-plugins"
    REMOVE_PARSERS = "remove-parsers"
    VERIFY_EDITOR = "verify-editor"


Language = Literal["python", "typescript_vue", "dart_flutter", "cpp"]


class Settings(PluginSettings):
    languages: list[Language] = Field(
        default_factory=list,
        max_length=4,
        title="Language profiles",
        json_schema_extra={"ui": {"widget": "selection_list"}},
    )
    clipboard: bool = Field(default=True, title="Use Wayland clipboard when available")
    primary_editor: bool = Field(default=True, title="Set Neovim as the login editor")


class Search(Inputs):
    exclude_globs: list[str] = Field(min_length=1, max_length=128)

    @field_validator("exclude_globs")
    @classmethod
    def verify_globs(cls, values: list[str]) -> list[str]:
        if any(not v or not v.isprintable() or v.startswith("!") or len(v) > 512 for v in values):
            raise ValueError("Invalid editor search exclusion glob")
        return values


def lua_list(values: list[str]) -> str:
    return "{" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "}"


class Plugin(DebianPackages, PublishedAssets):
    executable = "/usr/bin/nvim"
    settings_model = Settings
    package_folder = Path(__file__).parent
    file_actions = frozenset({LifecycleAction.CONFIGURE})
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: Inputs,
        PluginAction.INSTALL_PLUGINS: Inputs,
        PluginAction.INSTALL_PARSERS: Inputs,
        PluginAction.REMOVE_PARSERS: Inputs,
        PluginAction.VERIFY_EDITOR: Inputs,
    }

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

    async def verify(self, plan: Plan, settings: PluginSettings, inputs: Inputs) -> Result:
        if plan.resources and all(r.kind == "git-checkout" for r in plan.resources):
            return await asyncio.to_thread(extensions.verify_plugins, plan)
        if (
            plan.resources
            and all(r.kind == "parser" for r in plan.resources)
            and isinstance(settings, Settings)
        ):
            if all(row.effect == "remove" for row in plan.resources):
                absent = await asyncio.to_thread(
                    lambda: all(not os.path.lexists(row.identity) for row in plan.resources)
                )
                return Result(
                    outcome=Outcome.COMPLETE if absent else Outcome.PARTIAL,
                    message="Checked the selected parser files",
                    resources=plan.resources,
                )
            return await asyncio.to_thread(
                extensions.verify_parsers, plan, list(settings.languages)
            )
        if not plan.resources and isinstance(settings, Settings):
            return await asyncio.to_thread(
                extensions.verify_parsers, plan, list(settings.languages)
            )
        return await super().verify(plan, settings, inputs)

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        if not isinstance(settings, Settings):
            raise ValueError("Unexpected editor settings")
        root = inside_home(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))
        resources = self.package_folder / "resources"
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
        palette = desktop_context()
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


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("neovim", action_id, plan)
