"""Render appearance values from the packaged desktop contract."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined
from pydantic import JsonValue

from setup_system_desktop_theme.palette import desktop_style, palette


def desktop_context(directories: dict[str, Path], root: Path | None = None) -> dict[str, JsonValue]:
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
        "xdg": {k: str(p).replace("'", "'\"'\"'") for k, p in directories.items()},
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
