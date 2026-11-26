"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction, ServiceAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_docker import configuration, mode, registry
from setup_plugin_docker.actions import PluginActions
from setup_plugin_docker.enums import DockerAction
from setup_plugin_docker.models import Context, Image, Settings
from setup_plugin_docker.registry import Registry as Registry


class Plugin(PluginActions):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = {
        "install": "system.packages",
        "start-system-service": "system.packages",
        "stop-system-service": "system.packages",
        "uninstall": "system.packages",
        "update": "system.packages",
    }
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
        LifecycleAction.CHECK: Inputs,
        LifecycleAction.CONFIGURE: configuration.Daemon,
        DockerAction.REMOVE_OWNED_CONTEXTS: configuration.RemoveContexts,
        DockerAction.CREATE_CONTEXT: Context,
        DockerAction.START_SERVICE: Inputs,
        DockerAction.STOP_SERVICE: Inputs,
        DockerAction.LOGIN: registry.SignIn,
        DockerAction.LOGOUT: Registry,
        DockerAction.SETUP_MODE: mode.DaemonMode,
        DockerAction.PRUNE: Image,
        ServiceAction.START: Inputs,
        ServiceAction.STOP: Inputs,
    }
