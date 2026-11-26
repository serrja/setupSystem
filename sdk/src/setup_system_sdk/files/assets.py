"""Render a trusted desktop asset with the shared palette and native screen geometry."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined
from platformdirs import user_cache_path, user_config_path, user_data_path, user_state_path
from pydantic import JsonValue

from setup_system_sdk.enums.operation import Outcome
from setup_system_sdk.files.backend import FilesBackend
from setup_system_sdk.files.palette import desktop_style, palette
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


def desktop_context(root: Path) -> dict[str, JsonValue]:
    colors = palette(root)
    style = desktop_style(root)
    values: dict[str, JsonValue] = dict(colors)
    for name, color in colors.items():
        values[name + "_hex"] = color[1:]
        values[name + "_rgb"] = ", ".join(str(int(color[n : n + 2], 16)) for n in (1, 3, 5))
    return {
        **values,
        "fonts": style.fonts.model_dump(),
        "auth_screen": dict(style.auth_screen.tokens(colors)),
        "xdg": {k: str(p).replace("'", "'\"'\"'") for k, p in desktop_directories().items()},
    }


def render_desktop(source: Path, context: dict[str, JsonValue]) -> bytes:
    """Template source belongs to the accepted package. Context values are data."""
    environment = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        variable_start_string="@{",
        variable_end_string="}@",
        block_start_string="[%",
        block_end_string="%]",
        comment_start_string="[#",
        comment_end_string="#]",
    )
    return environment.from_string(source.read_text()).render(render_ctx=context).encode()


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
                    or previous.ownership == "preexisting"
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
