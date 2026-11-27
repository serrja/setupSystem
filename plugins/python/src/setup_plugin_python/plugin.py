"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction

from setup_plugin_python.actions import PluginActions
from setup_plugin_python.models import Settings


class Plugin(PluginActions):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = dict.fromkeys(
        (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL),
        "system.packages",
    )
    settings_model = Settings
