"""Resolve action-only workstation profiles against installed package descriptors."""

import asyncio
import tomllib
from graphlib import TopologicalSorter
from pathlib import Path

from pydantic import Field

from setup_system.configuration import Settings
from setup_system.configuration.store import load_values
from setup_system.plugin_manager import PluginManager, Snapshot
from setup_system_sdk import ActionId, Identifier, Inputs


class Selection(Inputs):
    plugin: Identifier
    action: ActionId
    profile: Identifier = "default"


class WorkstationProfile(Inputs):
    name: str
    description: str
    actions: tuple[Selection, ...] = Field(min_length=1, max_length=128)


def read_profiles(root: Path) -> dict[str, WorkstationProfile]:
    profiles = {}
    for path in sorted((root / "profiles").glob("*.toml")):
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("Invalid packaged profile")
        profiles[path.stem] = WorkstationProfile.model_validate(tomllib.loads(path.read_text()))
    return profiles


async def preview_profile(
    manager: PluginManager, store: Settings, profile: WorkstationProfile
) -> list[Snapshot]:
    ordered = TopologicalSorter[str]()
    selected = {row.plugin for row in profile.actions}
    for plugin in sorted(selected):
        activation = await manager.activation(plugin)
        ordered.add(plugin, *(dep for dep in activation.manifest.dependencies if dep in selected))
    results = []
    for plugin in ordered.static_order():
        for row in profile.actions:
            if row.plugin != plugin:
                continue
            state = await manager.activation(plugin)
            saved = await asyncio.to_thread(
                load_values, store.for_version(state.manifest.settings_version), plugin, row.profile
            )
            results.append(await manager.preview(plugin, row.action, {}, {}, saved=saved.values))
    return results
