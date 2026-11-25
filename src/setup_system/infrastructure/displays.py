"""Discover connected outputs and temporarily preview validated monitor settings."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from types import FrameType

from pydantic import ConfigDict, Field, JsonValue, StrictBool, StrictFloat, StrictInt, TypeAdapter

from setup_system.domain.contracts import ContractModel
from setup_system.domain.displays import DisplayOutput, MonitorLayout
from setup_system.infrastructure.processes import run_command

PREVIEW_SECONDS = 12


class OutputStatus(ContractModel):
    # hyprctl also returns hardware and renderer metadata outside the workstation contract.
    model_config = ConfigDict(extra="ignore")
    disabled: StrictBool = False
    mirror: str = Field(default="none", alias="mirrorOf")


class OutputFacts(OutputStatus):
    name: str
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    refresh: StrictFloat = Field(alias="refreshRate", gt=0)
    modes: tuple[str, ...] = Field(alias="availableModes")
    scale: StrictFloat
    x: StrictInt
    y: StrictInt


def _hyprctl(*args: str) -> str:
    if os.geteuid() == 0 or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        raise ValueError("Monitor layout needs a running user Hyprland session")
    try:
        result = run_command(["/usr/bin/hyprctl", *args], timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Hyprland monitor control is unavailable") from exc
    except subprocess.SubprocessError as exc:
        raise RuntimeError("Hyprland did not return the requested monitor information") from exc
    if result.returncode or len(result.stdout) > 262144:
        raise RuntimeError("Hyprland did not return the requested monitor information")
    return result.stdout.strip()


def discover_outputs() -> tuple[DisplayOutput, ...]:
    """Expose only active, addressable outputs with advertised modes."""
    result: list[DisplayOutput] = []
    for row in TypeAdapter(tuple[dict[str, JsonValue], ...]).validate_json(
        _hyprctl("-j", "monitors")
    ):
        status = OutputStatus.model_validate(row)
        if status.disabled or status.mirror != "none":
            continue
        facts = OutputFacts.model_validate(row)
        modes = tuple(mode.removesuffix("Hz") for mode in facts.modes)
        resolution = f"{facts.width}x{facts.height}"
        candidates = [m for m in modes if m.partition("@")[0] == resolution]
        if not candidates:
            continue
        closest = min(candidates, key=lambda m: abs(float(m.partition("@")[2]) - facts.refresh))
        layout = MonitorLayout(
            output=facts.name, mode=closest, scale=facts.scale, x=facts.x, y=facts.y
        )
        result.append(DisplayOutput(current=layout, modes=modes))
    return tuple(result)


def validate_layouts(layouts: tuple[MonitorLayout, ...]) -> tuple[MonitorLayout, ...]:
    if not layouts:
        return ()
    current = {item.current.output: item for item in discover_outputs()}
    if len(layouts) != len({layout.output for layout in layouts}):
        raise ValueError("An output can only appear once in a monitor layout")
    for layout in layouts:
        observed = current.get(layout.output)
        if observed is None or layout.mode not in observed.modes:
            raise ValueError(
                f"Output or mode is no longer available: {layout.output} {layout.mode}"
            )

    return tuple(current[layout.output].current for layout in layouts)


def _matches(layouts: tuple[MonitorLayout, ...]) -> bool:
    observed = {item.current.output: item.current for item in discover_outputs()}
    return all(observed.get(layout.output) == layout for layout in layouts)


def _wait_for(layouts: tuple[MonitorLayout, ...]) -> bool:
    deadline = time.monotonic() + 2
    while not _matches(layouts):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _preview(layouts: tuple[MonitorLayout, ...]) -> None:
    original = validate_layouts(layouts)
    if not layouts:
        raise ValueError("Select an output to preview")
    try:
        if _hyprctl("eval", "\n".join(layout.rule() for layout in layouts)) != "ok":
            raise RuntimeError("The compositor refused the preview layout")
        if not _wait_for(layouts):
            raise RuntimeError("The connected outputs did not switch to the requested layout")
        time.sleep(PREVIEW_SECONDS)
    finally:
        if _hyprctl("reload") != "ok":
            raise RuntimeError("Could not reload saved monitor settings")
        if not _wait_for(original):
            # A client may have changed the live layout since the configuration was saved.
            if _hyprctl(
                "eval", "\n".join(layout.rule() for layout in original)
            ) != "ok" or not _wait_for(original):
                raise RuntimeError("Monitor recovery could not verify the original outputs")


def preview_layout(layouts: tuple[MonitorLayout, ...]) -> None:
    """Restore the previous layout even on interruption of the dedicated Make task."""

    def interrupted(_number: int, _frame: FrameType | None) -> None:
        raise RuntimeError("Monitor preview interrupted")

    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        _preview(layouts)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
