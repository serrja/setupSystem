"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.schemas.common import Inputs

from setup_plugin_cleanup.actions import PluginActions
from setup_plugin_cleanup.models import Candidates, PurgeCandidates, Settings

from .enums import Mode


class Plugin(PluginActions):
    delegated_step_key = "cleanup"
    delegated_actions: ClassVar[dict[str, str]] = dict.fromkeys(Mode, "system.package-cleanup")
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        Mode.REMOVE: Candidates,
        Mode.PURGE: PurgeCandidates,
        Mode.AUTOREMOVE: Candidates,
    }
