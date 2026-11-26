"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_greeter.actions import PluginActions
from setup_plugin_greeter.actions import activate as activate
from setup_plugin_greeter.actions import apply_native as apply_native
from setup_plugin_greeter.enums import PluginAction
from setup_plugin_greeter.models import Confirmation as Confirmation
from setup_plugin_greeter.models import Settings


class Plugin(PluginActions):
    settings_model = Settings
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = {
        "install": "system.packages",
        "uninstall": "system.packages",
        "update": "system.packages",
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
        LifecycleAction.CHECK: Inputs,
        PluginAction.ACTIVATE_LOGIN: Inputs,
    }
