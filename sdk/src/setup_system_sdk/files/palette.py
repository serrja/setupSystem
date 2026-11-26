"""Read validated colors from the installed shared palette."""

import tomllib
from pathlib import Path

from setup_system_sdk.files.auth_screen import DesktopStyle
from setup_system_sdk.files.color_palette import DesktopPalette


def palette(root: Path) -> dict[str, str]:
    with (root / "palette.toml").open("rb") as stream:
        return DesktopPalette.model_validate(tomllib.load(stream)).model_dump(exclude={"name"})


def desktop_style(root: Path) -> DesktopStyle:
    """Load the installed typography and shared native authentication geometry."""
    with (root / "desktop.toml").open("rb") as stream:
        return DesktopStyle.model_validate(tomllib.load(stream))
