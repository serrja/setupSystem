"""Validated Debian capability requests and authorization limits."""

from typing import Literal

from pydantic import Field
from setup_system_sdk.enums.lifecycle import LifecycleAction, PackageAction, ServiceAction
from setup_system_sdk.resources import Resource
from setup_system_sdk.schemas.common import Identifier, Inputs, Values
from setup_system_sdk.settings import PluginSettings

from setup_plugin_debian.adapters.apt import PackageName


class PackageRequest(Inputs):
    consumer: Identifier
    action: (
        Literal[LifecycleAction.INSTALL, LifecycleAction.UPDATE, LifecycleAction.UNINSTALL]
        | PackageAction
        | ServiceAction
    )
    inventory: tuple[PackageName, ...] = Field(default=(), max_length=64)
    inputs: Values = Field(default_factory=dict)
    evidence: tuple[Resource, ...] | None = None


class TerminalOptions(Inputs):
    prompt_timeout_seconds: float = Field(default=30, gt=0)
    idle_timeout_seconds: float = Field(default=90, gt=0)
    termination_grace_seconds: float = Field(default=2, gt=0)


class RecoveryQuery(Inputs):
    owner: Identifier
    receipt: str | None = None
    release_digest: str
    operation: str
    snapshot: Values


Settings = PluginSettings
