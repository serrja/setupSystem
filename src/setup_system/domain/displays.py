"""Settings and observed modes for connected compositor outputs."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class MonitorLayout(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    output: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
    mode: str = Field(pattern=r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}@[0-9]{1,4}(\.[0-9]{1,5})?$")
    scale: float = Field(ge=0.25, le=8, allow_inf_nan=False)
    x: int = Field(ge=-65535, le=65535)
    y: int = Field(ge=-65535, le=65535)

    def rule(self) -> str:
        """All string values have a closed ASCII grammar at the settings boundary."""
        return (
            f'hl.monitor({{ output = "{self.output}", mode = "{self.mode}", '
            f'position = "{self.x}x{self.y}", scale = {self.scale} }})'
        )


@dataclass(frozen=True)
class DisplayOutput:
    current: MonitorLayout
    modes: tuple[str, ...]
