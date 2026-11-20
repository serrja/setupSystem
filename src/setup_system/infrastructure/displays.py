"""Discover connected outputs and temporarily preview validated monitor settings."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from pydantic import TypeAdapter

from setup_system.domain.displays import DisplayOutput, MonitorLayout
from setup_system.infrastructure.mutation import user_operation

PREVIEW_SECONDS = 12
_LAYOUTS = TypeAdapter(tuple[MonitorLayout, ...])


def _hyprctl(*args: str) -> str:
    if os.geteuid() == 0 or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        raise ValueError("Monitor layout needs a running user Hyprland session")
    try:
        result = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Hyprland monitor control is unavailable") from exc
    if result.returncode or len(result.stdout) > 262144:
        raise RuntimeError("Hyprland did not return the requested monitor information")
    return result.stdout.strip()


def discover_outputs() -> tuple[DisplayOutput, ...]:
    """Expose only active, addressable outputs with advertised modes."""
    result: list[DisplayOutput] = []
    for row in json.loads(_hyprctl("-j", "monitors")):
        if row.get("disabled") or row.get("mirrorOf", "none") != "none":
            continue
        modes = tuple(str(mode).removesuffix("Hz") for mode in row["availableModes"])
        resolution = f"{row['width']}x{row['height']}"
        candidates = [m for m in modes if m.partition("@")[0] == resolution]
        if not candidates:
            continue
        closest = min(
            candidates, key=lambda m: abs(float(m.partition("@")[2]) - row["refreshRate"])
        )
        layout = MonitorLayout(
            output=row["name"], mode=closest, scale=row["scale"], x=row["x"], y=row["y"]
        )
        result.append(DisplayOutput(layout, modes))
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
    """Run in a Textual worker. A separate session restores saved rules even if the TUI exits."""
    result = subprocess.run(
        [sys.executable, "-I", "-m", __name__],
        input=_LAYOUTS.dump_json(layouts),
        capture_output=True,
        start_new_session=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.decode(errors="replace").strip() or "Monitor preview failed"
        )


if __name__ == "__main__":
    try:

        def interrupt(_number: int, _frame: object) -> None:
            raise RuntimeError("Monitor preview interrupted")

        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGHUP, interrupt)
        with user_operation():
            _preview(_LAYOUTS.validate_json(sys.stdin.buffer.read(32768)))
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
