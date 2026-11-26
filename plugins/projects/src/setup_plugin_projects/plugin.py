"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.schemas.common import Inputs

from setup_plugin_projects.action_inputs import (
    CatalogTemplate,
    Creation,
    Destination,
    Project,
    Template,
)
from setup_plugin_projects.actions import PluginActions
from setup_plugin_projects.enums import PluginAction
from setup_plugin_projects.settings import Settings


class Plugin(PluginActions):
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        PluginAction.INSPECT_PROJECTS: Inputs,
        PluginAction.OPEN_EDITOR: Project,
        PluginAction.OPEN_CODEX: Project,
        PluginAction.TEMPLATE_VERSIONS: CatalogTemplate,
        PluginAction.PREPARE_DIRECTORIES: Inputs,
        PluginAction.TEMPLATE_CACHE: Template,
        PluginAction.TEMPLATE_CREATE: Creation,
        PluginAction.TEMPLATE_UPDATE: Destination,
    }
