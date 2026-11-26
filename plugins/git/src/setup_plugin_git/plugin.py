"""Declare the plugin settings, actions and capability handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from setup_system_sdk.enums.lifecycle import LifecycleAction, PackageAction
from setup_system_sdk.schemas.common import Inputs

from setup_plugin_git import keys, local_keys
from setup_plugin_git.actions import PluginActions
from setup_plugin_git.enums import GitAction
from setup_plugin_git.identities import (
    Identity,
    ImportIdentity,
    Style,
    UseIdentity,
)
from setup_plugin_git.models import CleanupKeys, RetiredPackage, Settings


class Plugin(PluginActions):
    delegated_step_key = "packages"
    delegated_actions: ClassVar[dict[str, str]] = {
        "install": "system.packages",
        "remove-retired-package": "system.packages",
        "uninstall": "system.packages",
        "update": "system.packages",
    }
    settings_model = Settings
    action_inputs: ClassVar[dict[StrEnum, type[Inputs]]] = {
        **dict.fromkeys(
            (LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL), Inputs
        ),
        PackageAction.REMOVE_RETIRED: RetiredPackage,
        GitAction.UPLOAD_KEY: keys.Upload,
        GitAction.VERIFY_SSH: keys.Probe,
        GitAction.REVOKE_KEY: keys.Revoke,
        LifecycleAction.CONFIGURE: Identity,
        GitAction.CONFIGURE_STYLE: Style,
        GitAction.IMPORT_IDENTITY: ImportIdentity,
        GitAction.USE_IDENTITY: UseIdentity,
        LifecycleAction.CHECK: Inputs,
        GitAction.REMOVE_OWNED_KEYS: CleanupKeys,
        GitAction.GENERATE_KEY: local_keys.Generate,
        GitAction.REMOVE_LOCAL_KEYS: local_keys.RemoveLocal,
    }
