"""Render packaged native configuration files using the Ansible template delimiters."""

from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, StrictUndefined


def render_asset(source: Path, context: Mapping[str, str | Mapping[str, str | int]]) -> bytes:
    environment = Environment(
        undefined=StrictUndefined,
        variable_start_string="@{",
        variable_end_string="}@",
        block_start_string="[%",
        block_end_string="%]",
        comment_start_string="[#",
        comment_end_string="#]",
        keep_trailing_newline=True,
        autoescape=False,
    )
    return environment.from_string(source.read_text(encoding="utf-8")).render(context).encode()
