"""Public SDK contracts shared by the host and plugin workers."""

from setup_system_sdk.backend import Backend
from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.manifests import Action, Manifest
from setup_system_sdk.operations import (
    ActionEvent,
    Executor,
    Plan,
    Receipt,
    Result,
    Snapshot,
    StepPlan,
)
from setup_system_sdk.resources import Resource
from setup_system_sdk.settings import Inputs, PluginSettings
from setup_system_sdk.version import PLUGIN_GROUP, SDK_VERSION, UI_GROUP

__all__ = [
    "PLUGIN_GROUP",
    "SDK_VERSION",
    "UI_GROUP",
    "Action",
    "ActionEvent",
    "Backend",
    "Executor",
    "Inputs",
    "Manifest",
    "Outcome",
    "Plan",
    "PluginSettings",
    "Receipt",
    "Resource",
    "Result",
    "Snapshot",
    "StepPlan",
]
