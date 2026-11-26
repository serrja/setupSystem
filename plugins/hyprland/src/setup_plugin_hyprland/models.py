"""Settings and observed modes for connected compositor outputs."""

from typing import Annotated

from pydantic import Field, StrictFloat, StrictInt

from setup_system.execution.contracts import ContractModel

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
