"""Render one checked-in Catppuccin palette into existing managed desktop resources."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from setup_system.domain.settings import ThemeSettings

RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def palette() -> dict[str, str]:
    document = tomllib.loads((RESOURCE_ROOT / "themes/catppuccin_mocha_green.toml").read_text())
    return {key: str(value) for key, value in document.items()}


def render(content: str, settings: ThemeSettings | None = None) -> str:
    selected = ThemeSettings() if settings is None else settings
    colors = palette()
    values: dict[str, object] = {key: value for key, value in colors.items()}
    values.update({key + "_hex": value.removeprefix("#") for key, value in colors.items()})
    values.update(
        {
            key + "_rgb": ", ".join(str(int(value[index : index + 2], 16)) for index in (1, 3, 5))
            for key, value in colors.items()
            if value.startswith("#")
        }
    )
    values.update(
        rounding=selected.rounding,
        opacity=selected.opacity,
        panel_height=30 if selected.compact_panel else 36,
        effects="true" if selected.effects else "false",
    )
    content = re.sub(r"\{\{\s*([a-z_0-9]+)\s*\}\}", r"@{ \1 }@", content)
    return (
        Environment(
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
        .from_string(content)
        .render(values)
    )


def preview(settings: ThemeSettings) -> str:
    colors = palette()
    return (
        f"Catppuccin Mocha · green {colors['green']} · base {colors['base']}\n"
        f"Top floating panel: {30 if settings.compact_panel else 36}px; "
        f"opacity {settings.opacity:.2f}; rounding {settings.rounding}px; "
        f"effects {'on' if settings.effects else 'off'}; preferred monitor mode / automatic scale."
    )


def configure_targets(
    targets: dict[Path, bytes], settings: ThemeSettings, *, keyboard: str | None = None
) -> dict[Path, bytes]:
    """Theme-only edits never require package reinstallation."""
    if keyboard not in {None, "us", "us,ru"}:
        raise ValueError("Unsupported keyboard layout")
    output = dict(targets)
    for path, data in targets.items():
        if path.name == "waybar.json":
            document = json.loads(data)
            document["height"] = 30 if settings.compact_panel else 36
            output[path] = (json.dumps(document, indent=2) + "\n").encode()
        elif path.name in {"profile.lua", "style.css", "hyprlock.conf", "mako.conf"}:
            text = data.decode()
            if path.name == "profile.lua":
                if keyboard is not None:
                    text = text.replace('kb_layout = "us,ru"', 'kb_layout = "' + keyboard + '"')
                text = text.replace("rounding = 12", f"rounding = {settings.rounding}")
                text = text.replace(
                    "enabled = false", f"enabled = {'true' if settings.effects else 'false'}"
                )
            elif path.name == "style.css":
                text = text.replace("0.96)", f"{settings.opacity})")
                text = re.sub(
                    r"border-radius: [0-9]+px", f"border-radius: {settings.rounding}px", text
                )
            elif path.name == "hyprlock.conf":
                text = re.sub(r"rounding = [0-9]+", f"rounding = {settings.rounding}", text)
            elif path.name == "mako.conf":
                text = re.sub(r"border-radius=[0-9]+", f"border-radius={settings.rounding}", text)
            output[path] = text.encode()
    return output
