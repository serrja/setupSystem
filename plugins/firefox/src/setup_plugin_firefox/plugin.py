"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_firefox.actions import PluginActions
from setup_plugin_firefox.actions import apply_native as apply_native
from setup_plugin_firefox.enums import PolicyAction
from setup_plugin_firefox.models import Settings


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
        LifecycleAction.CONFIGURE: Inputs,
        PolicyAction.REMOVE_POLICY: Inputs,
    }
