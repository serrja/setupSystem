"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.plugin import PluginSettings
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_administration.actions import PluginActions
from setup_plugin_administration.actions import apply_native as apply_native
from setup_plugin_administration.enums import PluginAction
from setup_plugin_administration.models import Login


class Plugin(PluginActions):
    settings_model = PluginSettings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        PluginAction.GRANT_SUDO: Login,
        PluginAction.VERIFY_SUDO: Login,
    }
