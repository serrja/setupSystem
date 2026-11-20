"""Load the release's shared palette at user and stdlib-only system boundaries."""

import re
import tomllib
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def palette(resources: Path = RESOURCE_ROOT) -> dict[str, str]:
    document = tomllib.loads((resources / "themes/catppuccin_mocha_green.toml").read_text())
    document.pop("name", None)
    if not all(
        isinstance(value, str) and re.fullmatch(r"#[0-9a-f]{6}", value)
        for value in document.values()
    ):
        raise ValueError("Invalid desktop palette color")
    return document
