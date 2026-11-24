"""Only monitored, advertised display modes enter a preview or desktop profile."""

import json

import pytest
from pydantic import ValidationError

from setup_system.domain.displays import MonitorLayout
from setup_system.infrastructure import displays


def test_monitor_selection_and_failed_preview_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    output = {
        "name": "DP-2",
        "availableModes": ["1920x1080@60.00Hz", "1280x720@60.00Hz"],
        "width": 1920,
        "height": 1080,
        "refreshRate": 59.998,
        "scale": 1,
        "x": 0,
        "y": 0,
    }
    commands = []

    def query(*args: str) -> str:
        commands.append(args)
        if args == ("-j", "monitors"):
            return json.dumps([output])
        if args == ("reload",):
            return "ok"
        return "compositor rejected layout"

    monkeypatch.setattr(displays, "_hyprctl", query)
    (observed,) = displays.discover_outputs()
    assert observed.current.mode == "1920x1080@60.00"
    preview = observed.current.validated(mode="1280x720@60.00", scale=1.25)
    with pytest.raises(RuntimeError, match="refused"):
        displays._preview((preview,))
    assert ("eval", preview.rule()) in commands and ("reload",) in commands
    with pytest.raises(ValueError, match="no longer available"):
        displays.validate_layouts((preview.validated(output="DP-3"),))
    with pytest.raises(ValueError, match="once"):
        displays.validate_layouts((preview, preview))
    lua = preview.rule()
    assert '"DP-2"' in lua and '"1280x720@60.00"' in lua and "scale = 1.25" in lua


def test_display_values_reject_unsupported_syntax() -> None:
    with pytest.raises(ValidationError):
        MonitorLayout(output='arbitrary" string', mode="preferred", x=0, y=0, scale=1)
