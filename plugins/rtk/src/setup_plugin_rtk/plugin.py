"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_rtk.actions import PluginActions
from setup_plugin_rtk.models import Settings


class Plugin(PluginActions):
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = dict.fromkeys(
        (
            LifecycleAction.INSTALL,
            LifecycleAction.UPDATE,
            LifecycleAction.CHECK,
            LifecycleAction.UNINSTALL,
        ),
        Inputs,
    )
