"""Shared visual geometry for the native login and lock screens."""

from collections.abc import Mapping
from typing import Annotated

from pydantic import Field

from setup_system.domain.contracts import ContractModel


class AuthScreen(ContractModel):
    """Dimensions use logical pixels at the compositor's current scale."""

    font: Annotated[str, Field(pattern=r"^[A-Za-z0-9 ]{1,48}$")] = "Noto Sans"
    font_size: Annotated[int, Field(ge=10, le=48)] = 16
    input_width: Annotated[int, Field(ge=384, le=640)] = 384
    input_height: Annotated[int, Field(ge=32, le=96)] = 48
    spacing: Annotated[int, Field(ge=8, le=48)] = 16
    radius: Annotated[int, Field(ge=0, le=32)] = 12
    border: Annotated[int, Field(ge=1, le=4)] = 2

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
