"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_regional.actions import Confirmation as Confirmation
from setup_plugin_regional.actions import PluginActions
from setup_plugin_regional.actions import apply_native as apply_native
from setup_plugin_regional.enums import PluginAction
from setup_plugin_regional.models import Keyboard, Locale, Settings, Timezone


class Plugin(PluginActions):
    settings_model = Settings
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = {
        "install": "system.packages",
        "update": "system.packages",
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        LifecycleAction.INSTALL: Inputs,
        LifecycleAction.UPDATE: Inputs,
        PluginAction.LOCALE: Locale,
        PluginAction.KEYBOARD: Keyboard,
        PluginAction.TIMEZONE: Timezone,
    }
