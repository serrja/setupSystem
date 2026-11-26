"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_codex.actions import PluginActions
from setup_plugin_codex.enums import ClientAction
from setup_plugin_codex.models import McpServer, Project, Settings, SignIn


class Plugin(PluginActions):
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.INSTALL: Inputs,
        LifecycleAction.UPDATE: Inputs,
        LifecycleAction.UNINSTALL: Inputs,
        LifecycleAction.CONFIGURE: Project,
        ClientAction.LOGIN: SignIn,
        ClientAction.STATUS: Inputs,
        ClientAction.LOGOUT: Inputs,
        ClientAction.MCP: Inputs,
        ClientAction.CONFIGURE_MCP: McpServer,
        ClientAction.SKILLS: Inputs,
    }
