"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from typing import ClassVar

from setup_system_sdk.providers import Provider

from setup_plugin_debian.actions import PluginActions
from setup_plugin_debian.adapters.recovery import RecoveryProvider
from setup_plugin_debian.cleanup_provider import CleanupProvider
from setup_plugin_debian.models import Settings
from setup_plugin_debian.providers import PackageProvider


class Plugin(PluginActions):
    settings_model = Settings
    providers: ClassVar[dict[str, Provider]] = {
        "system.package-cleanup": CleanupProvider(),
        "system.packages": PackageProvider(),
        "operation.recovery": RecoveryProvider(),
    }
