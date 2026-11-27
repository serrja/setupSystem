"""Derive a small closed UI vocabulary from existing validated manifest declarations."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue
from setup_system_sdk.manifests import Manifest
from setup_system_sdk.schemas.common import ActionId


class Feature(StrEnum):
    SETTINGS = "settings"
    SAVE_SETTINGS = "save_settings"
    ACTIONS = "actions"
    ACCOUNTS = "accounts"
    HELP = "help"
    PROVIDER_STATUS = "provider_status"


class FeatureAvailability(BaseModel):
    supported: bool
    ready: bool = False
    reason: str = ""


class PluginViewState(BaseModel):
    features: dict[Feature, FeatureAvailability]
    actions: dict[ActionId, FeatureAvailability] = Field(default_factory=dict)


def declared_features(manifest: Manifest, resources: Path | None = None) -> PluginViewState:
    properties = manifest.settings_schema.get("properties", {})
    fields = properties if isinstance(properties, dict) else {}

    def is_writable(value: JsonValue) -> bool:
        if not isinstance(value, dict):
            return False
        hints = value.get("ui")
        return not value.get("readOnly") and not (isinstance(hints, dict) and hints.get("readonly"))

    writable = any(is_writable(value) for value in fields.values())
    declared = {
        Feature.SETTINGS: bool(fields),
        Feature.SAVE_SETTINGS: writable,
        Feature.ACTIONS: any(action.page == "actions" for action in manifest.actions),
        Feature.ACCOUNTS: any(action.page == "accounts" for action in manifest.actions),
        Feature.HELP: bool(resources and (resources / "help.md").is_file()),
        Feature.PROVIDER_STATUS: bool(manifest.provides),
    }
    return PluginViewState(
        features={key: FeatureAvailability(supported=value) for key, value in declared.items()},
        actions={action.id: FeatureAvailability(supported=True) for action in manifest.actions},
    )
