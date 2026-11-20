"""Render trusted desktop templates with one validated theme and font context."""

from __future__ import annotations

from typing import Protocol

from jinja2 import Environment, StrictUndefined

from setup_system.domain.displays import MonitorLayout
from setup_system.domain.settings import ThemeSettings
from setup_system.infrastructure.palette import RESOURCE_ROOT as RESOURCE_ROOT
from setup_system.infrastructure.palette import palette as palette

_TEMPLATES = Environment(
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


class FontValues(Protocol):
    family: str
    emoji_fallback: str
    ui_size: int
    terminal_size: int
    panel_size: int


def render(
    content: str,
    settings: ThemeSettings | None = None,
    *,
    keyboard: str | None = None,
    monitors: tuple[MonitorLayout, ...] = (),
    fonts: FontValues | None = None,
) -> str:
    """Render template fields only. Lua and shell braces outside fields stay literal."""
    if keyboard not in {None, "us", "us,ru"}:
        raise ValueError("Unsupported keyboard layout")
    selected = settings or ThemeSettings()
    colors = palette()
    values: dict[str, object] = dict(colors)
    values.update({key + "_hex": color[1:] for key, color in colors.items()})
    values.update(
        {
            key + "_rgb": ", ".join(str(int(color[i : i + 2], 16)) for i in (1, 3, 5))
            for key, color in colors.items()
        }
    )
    return _TEMPLATES.from_string(content).render(
        values,
        rounding=selected.rounding,
        opacity=selected.opacity,
        panel_height=30 if selected.compact_panel else 36,
        effects="true" if selected.effects else "false",
        keyboard=keyboard,
        monitors=monitors,
        fonts=fonts,
    )


def preview(settings: ThemeSettings) -> str:
    colors = palette()
    return (
        f"Catppuccin Mocha · green {colors['green']} · base {colors['base']}\n"
        f"Top floating panel {30 if settings.compact_panel else 36}px · "
        f"opacity {settings.opacity:.2f} · rounding {settings.rounding}px · "
        f"effects {'on' if settings.effects else 'off'}"
    )
