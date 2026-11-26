"""Shared visual geometry for the native login and lock screens."""

from collections.abc import Mapping
from typing import Annotated

from pydantic import Field, StrictInt

from setup_system.execution.contracts import ContractModel

FontName = Annotated[str, Field(pattern=r"^[A-Za-z0-9 ]{1,48}$")]


class AuthScreen(ContractModel):
    """Dimensions use logical pixels at the compositor's current scale."""

    font: FontName
    font_size: Annotated[StrictInt, Field(ge=10, le=48)]
    input_width: Annotated[StrictInt, Field(ge=384, le=640)]
    input_height: Annotated[StrictInt, Field(ge=32, le=96)]
    spacing: Annotated[StrictInt, Field(ge=8, le=48)]
    radius: Annotated[StrictInt, Field(ge=0, le=32)]
    border: Annotated[StrictInt, Field(ge=1, le=4)]

    def tokens(self, colors: Mapping[str, str]) -> dict[str, str | int]:
        """Resolve the same layout and color roles for both native renderers."""
        return {
            **self.model_dump(),
            "card_width": self.input_width + 4 * self.spacing,
            "card_height": 2 * self.input_height + 6 * self.spacing,
            "background": colors["base"],
            "card": colors["mantle"],
            "input": colors["surface0"],
            "foreground": colors["text"],
            "accent": colors["green"],
            "error": colors["red"],
        }


class DesktopFonts(ContractModel):
    family: FontName
    emoji_fallback: FontName
    ui_size: Annotated[StrictInt, Field(ge=6, le=48)]
    terminal_size: Annotated[StrictInt, Field(ge=6, le=48)]
    panel_size: Annotated[StrictInt, Field(ge=6, le=48)]


class DesktopStyle(ContractModel):
    auth_screen: AuthScreen
    fonts: DesktopFonts
    panel_height: Annotated[StrictInt, Field(ge=16, le=128)]
    compact_panel_height: Annotated[StrictInt, Field(ge=16, le=128)]
