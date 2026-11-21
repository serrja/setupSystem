"""Read the named native colors from the selected release."""

from pathlib import Path

from setup_system.domain.palette import DesktopPalette
from setup_system.infrastructure.catalog_repository import read_contract
from setup_system.resource_locator import ResourceLocator


def palette(root: Path | None = None) -> dict[str, str]:
    path = ResourceLocator.for_root(root).resource("themes/catppuccin_mocha_green.toml")
    return read_contract(path, DesktopPalette).model_dump(exclude={"name"})
