"""Settings and observed modes for connected compositor outputs."""

from typing import Annotated

from pydantic import Field, StrictFloat, StrictInt
from setup_system_sdk.files.contracts import ContractModel
from setup_system_sdk.schemas.common import Inputs

AdvertisedMode = Annotated[
    str, Field(pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\.[0-9]{1,5})?$")
]


class MonitorLayout(ContractModel):
    output: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
    mode: AdvertisedMode
    scale: StrictFloat = Field(default=1, ge=0.25, le=8, allow_inf_nan=False)
    x: StrictInt = Field(default=0, ge=-65535, le=65535)
    y: StrictInt = Field(default=0, ge=-65535, le=65535)

    def rule(self) -> str:
        """All string values have a closed ASCII grammar at the settings boundary."""
        return (
            f'hl.monitor({{ output = "{self.output}", mode = "{self.mode}", '
            f'position = "{self.x}x{self.y}", scale = {self.scale} }})'
        )


class DisplayOutput(ContractModel):
    current: MonitorLayout
    modes: tuple[AdvertisedMode, ...]


class Display(Inputs):
    output: str = Field(
        description="Select a display connector reported by the current compositor session.",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "outputs"}},
    )
    mode: AdvertisedMode = Field(
        description="Choose an advertised display mode for the selected connector.",
        json_schema_extra={"ui": {"widget": "select", "choice_provider": "modes"}},
    )
    x: int = Field(
        description="Set the horizontal position of this output in the desktop layout.",
        ge=-65535,
        le=65535,
        default=0,
    )
    y: int = Field(
        description="Set the vertical position of this output in the desktop layout.",
        ge=-65535,
        le=65535,
        default=0,
    )
    scale: float = Field(
        description="Set the logical display scale for this output.",
        ge=0.25,
        le=8.0,
        allow_inf_nan=False,
        default=1.0,
    )
