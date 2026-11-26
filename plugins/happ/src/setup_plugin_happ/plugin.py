"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_happ.actions import PluginActions
from setup_plugin_happ.enums import PluginAction
from setup_plugin_happ.models import (
    ForgetAll,
    ForgetSubscription,
    Route,
    Settings,
    Subscription,
    SubscriptionID,
)


class Plugin(PluginActions):
    settings_model = Settings
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = {
        "install": "system.packages",
        "start-system-service": "system.packages",
        "stop-system-service": "system.packages",
        "uninstall": "system.packages",
        "update": "system.packages",
    }
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
        LifecycleAction.CHECK: Inputs,
        PluginAction.START_SYSTEM_SERVICE: Inputs,
        PluginAction.STOP_SYSTEM_SERVICE: Inputs,
        PluginAction.SAVE_SUBSCRIPTION: Subscription,
        PluginAction.IMPORT: SubscriptionID,
        PluginAction.UPDATE_SUBSCRIPTION: SubscriptionID,
        PluginAction.REMOVE_SUBSCRIPTION: SubscriptionID,
        PluginAction.FORGET_LOCAL: ForgetSubscription,
        PluginAction.FORGET_OWNED_FILES: ForgetAll,
        PluginAction.EXPORT_ROUTE: Route,
        PluginAction.RECONNECT: SubscriptionID,
    }
