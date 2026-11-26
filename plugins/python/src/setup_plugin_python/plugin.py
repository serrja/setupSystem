"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from setup_plugin_python.actions import PluginActions
from setup_plugin_python.models import Settings


class Plugin(PluginActions):
    settings_model = Settings
