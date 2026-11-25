"""Read validated colors from the installed shared palette."""

import tomllib
from pathlib import Path

from setup_system.domain.auth_screen import DesktopStyle
from setup_system.domain.palette import DesktopPalette
from setup_system.layout import INSTALLED


def palette(root: Path | None = None) -> dict[str, str]:
    root = root or INSTALLED
    with (root / "config/shared/palette.toml").open("rb") as stream:
        return DesktopPalette.model_validate(tomllib.load(stream)).model_dump(exclude={"name"})


def desktop_style(root: Path) -> DesktopStyle:
    """Load the installed typography and shared native authentication geometry."""
    with (root / "config/shared/desktop.toml").open("rb") as stream:
        return DesktopStyle.model_validate(tomllib.load(stream))
