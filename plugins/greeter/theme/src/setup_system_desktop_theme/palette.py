"""Read validated colors from the installed shared palette."""

import tomllib
from importlib.resources import files
from pathlib import Path

from setup_system_desktop_theme.auth_screen import DesktopStyle
from setup_system_desktop_theme.color_palette import DesktopPalette


def palette(root: Path | None = None) -> dict[str, str]:
    with ((root or Path(str(files("setup_system_desktop_theme")))) / "palette.toml").open(
        "rb"
    ) as stream:
        return DesktopPalette.model_validate(tomllib.load(stream)).model_dump(exclude={"name"})


def desktop_style(root: Path | None = None) -> DesktopStyle:
    """Load the installed typography and shared native authentication geometry."""
    with ((root or Path(str(files("setup_system_desktop_theme")))) / "desktop.toml").open(
        "rb"
    ) as stream:
        return DesktopStyle.model_validate(tomllib.load(stream))
