"""Named native desktop color roles supplied by a reviewed theme."""

from typing import Annotated, Literal

from pydantic import Field

from setup_system.domain.contracts import ContractModel

Color = Annotated[str, Field(pattern=r"^#[a-f0-9]{6}$")]


class DesktopPalette(ContractModel):
    name: Literal["catppuccin_mocha_green"]
    base: Color
    mantle: Color
    crust: Color
    surface0: Color
    surface1: Color
    surface2: Color
    text: Color
    subtext0: Color
    subtext1: Color
    green: Color
    teal: Color
    red: Color
    yellow: Color
    mauve: Color
    rosewater: Color
