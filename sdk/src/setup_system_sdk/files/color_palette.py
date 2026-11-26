"""Named native desktop color roles supplied by a reviewed theme."""

from typing import Annotated

from pydantic import Field

from setup_system_sdk.files.contracts import ContractModel

Color = Annotated[str, Field(pattern=r"^#[a-f0-9]{6}$")]


class DesktopPalette(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
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
