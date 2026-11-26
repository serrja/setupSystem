"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_obsidian.actions import PluginActions
from setup_plugin_obsidian.enums import PluginAction
from setup_plugin_obsidian.models import (
    CoreFeature,
    EditorSnippet,
    Hotkey,
    NewVault,
    OpenVault,
    Settings,
    Theme,
)


class Plugin(PluginActions):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = {
        "install": "system.packages",
        "uninstall": "system.packages",
        "update": "system.packages",
    }
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
        LifecycleAction.CHECK: Inputs,
        PluginAction.CREATE_VAULT: NewVault,
        PluginAction.OPEN: OpenVault,
        PluginAction.SET_THEME: Theme,
        PluginAction.CORE_FEATURE: CoreFeature,
        PluginAction.EDITOR_SNIPPET: EditorSnippet,
        PluginAction.HOTKEYS: Inputs,
        PluginAction.READ_HOTKEY: Hotkey,
        PluginAction.EDITOR: Inputs,
        PluginAction.ACCOUNT_SYNC: Inputs,
        PluginAction.COMMUNITY_PLUGINS: Inputs,
    }
