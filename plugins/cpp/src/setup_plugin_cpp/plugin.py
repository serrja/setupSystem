"""Publish clangd background-index settings and keep other YAML documents intact."""

from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

import yaml
from platformdirs import user_config_path
from pydantic import Field
from setup_system_sdk import Inputs, PluginSettings
from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.files.backend import FilesBackend
from setup_system_sdk.files.managed_text import managed_block
from setup_system_sdk.files.user_storage import capture_receipt, inside_home


class Settings(PluginSettings):
    background: Literal["Build", "Skip"] = Field(
        default="Build",
        title="Background index",
        description="Index the compilation database or only the files you open.",
    )


class Plugin(FilesBackend):
    executable = "/usr/bin/clangd"
    package_folder = Path(__file__).parent
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = dict.fromkeys(
        (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL),
        "system.packages",
    )
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
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
