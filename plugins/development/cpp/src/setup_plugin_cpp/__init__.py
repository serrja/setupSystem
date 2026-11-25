"""Publish clangd background-index settings and keep other YAML documents intact."""

from pathlib import Path
from typing import ClassVar, Literal

import yaml
from platformdirs import user_config_path
from pydantic import Field

from setup_system.domain.managed_text import managed_block
from setup_system.infrastructure.user_storage import capture_receipt, inside_home
from setup_system_sdk import Inputs, LifecycleAction, Plan, PluginSettings
from setup_system_sdk.apt import DebianPackages, apply_packages
from setup_system_sdk.native import FilesBackend


class Settings(PluginSettings):
    background: Literal["Build", "Skip"] = Field(
        default="Build",
        title="Background index",
        description="Index the compilation database or only the files you open.",
    )


class Plugin(DebianPackages, FilesBackend):
    executable = "/usr/bin/clangd"
    package_folder = Path(__file__).parent
    settings_model = Settings
    inputs: ClassVar[dict[str, type[Inputs]]] = {
        **DebianPackages.package_inputs,
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: Inputs,
    }
    file_actions = frozenset({LifecycleAction.CONFIGURE})

    def files(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> dict[Path, bytes]:
        options = Settings.model_validate(settings.model_dump())
        path = inside_home(user_config_path("clangd") / "config.yaml")
        captured = capture_receipt(path) if path.exists() else None
        current = captured.data.decode() if captured else ""
        text = managed_block(current, "---\nIndex:\n  Background: " + options.background)
        if any(v is not None and not isinstance(v, dict) for v in yaml.safe_load_all(text)):
            raise ValueError("Existing clangd configuration contains unsupported YAML")
        return {path: text.encode()}


def apply_native(action_id: str, settings: PluginSettings, inputs: Inputs, plan: Plan) -> None:
    apply_packages("cpp", action_id, plan)
