"""Render a trusted desktop asset with the shared palette and native screen geometry."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path, user_config_path, user_data_path, user_state_path
from pydantic import JsonValue
from setup_system_desktop_theme.palette import desktop_style as desktop_style
from setup_system_desktop_theme.palette import palette as palette
from setup_system_desktop_theme.rendering import desktop_context as theme_context
from setup_system_desktop_theme.rendering import render_desktop as render_desktop

from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.enums.resources import ResourceOwnership
from setup_system_sdk.files.backend import FilesBackend
from setup_system_sdk.files.user_storage import inside_home
from setup_system_sdk.plugin import PluginSettings, owned_resources
from setup_system_sdk.schemas.common import Inputs
from setup_system_sdk.schemas.operation import Plan


def desktop_directories() -> dict[str, Path]:
    return {
        k: inside_home(p)
        for k, p in {
            "config": user_config_path(),
            "data": user_data_path(),
            "state": user_state_path(),
            "cache": user_cache_path(),
        }.items()
    }


def desktop_context(root: Path | None = None) -> dict[str, JsonValue]:
    return theme_context(desktop_directories(), root)


class PublishedAssets(FilesBackend):
    """Replace whole files only after matching a verified previous publication."""

    def prepare(self, action_id: str, settings: PluginSettings, inputs: Inputs) -> Plan:
        plan = super().prepare(action_id, settings, inputs)
        if action_id not in self.file_actions:
            return plan
        owned = {(r.kind, r.identity): r for r in owned_resources()}
        for row in plan.resources:
            previous = owned.get((row.kind, row.identity))
            if (
                previous
                and previous.after is not None
                and (row.before != previous.after or row.permissions != previous.permissions)
            ):
                return Plan(
                    summary=f"Review local changes to {row.identity}.",
                    outcome=Outcome.BLOCKED,
                )
            if (
                row.before is not None
                and row.before != row.after
                and (
                    previous is None
                    or previous.ownership is ResourceOwnership.PREEXISTING
                    or previous.after != row.before
                    or previous.permissions != row.permissions
                )
            ):
                return Plan(
                    summary=f"Preserve the unmanaged or locally edited file {row.identity}.",
                    outcome=Outcome.BLOCKED,
                )
            if (
                row.permissions is not None
                and self.mode(Path(row.identity)) & 0o100
                and not row.permissions & 0o100
            ):
                return Plan(
                    summary=f"Review the executable mode on {row.identity}.",
                    outcome=Outcome.BLOCKED,
                )
        return plan
